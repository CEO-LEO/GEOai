"""
sources.py — แหล่งภาพแบบ adapter (spec 7.8: เพิ่มแหล่งภาพใหม่ได้โดยไม่ต้องแก้ส่วนอื่น)

แต่ละแหล่งบอก: ชื่อ feature (ลำดับคงที่), ภาพ ee.Image ของปีที่ต้องการ, ความละเอียด และวิธีแปลงค่าดิบ → feature
features.py รวมทุกแหล่งของชุด feature เป็นการเรียก GEE ครั้งเดียวต่อแปลง

Phase 4b (ยังไม่ทำ): THEOS-2 (GISTDA ~0.5 ม.) / ภาพโดรน → สร้างคลาสใหม่ตาม interface เดียวกัน แล้วเพิ่มใน FEATURE_SETS
ห้ามใส่ภาพที่ผ่าน super-resolution เป็นแหล่ง (spec 7.5)
"""

import statistics

MONTHS = range(1, 13)


class Source:
    name = ""
    scale = 10
    names: list[str] = []

    def image(self, ee, geom, year: int):
        raise NotImplementedError

    def to_features(self, raw: dict) -> dict[str, float | None]:
        return {n: raw.get(n) for n in self.names}


class SatelliteEmbedding(Source):
    """AlphaEarth Foundations Satellite Embedding V1 รายปี (64 มิติ 10 ม., CC BY 4.0)"""
    name = "emb"
    COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
    ATTRIBUTION = "The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind."
    names = [f"A{i:02d}" for i in range(64)]

    def image(self, ee, geom, year):
        return (ee.ImageCollection(self.COLLECTION).filterBounds(geom)
                .filterDate(f"{year}-01-01", f"{year + 1}-01-01").mosaic().select(self.names))


def _stats(prefix: str, vals: list[float | None]) -> dict[str, float | None]:
    v = [x for x in vals if x is not None]
    if len(v) < 3:
        return {f"{prefix}_mean": None, f"{prefix}_sd": None, f"{prefix}_min": None, f"{prefix}_max": None}
    return {f"{prefix}_mean": statistics.fmean(v), f"{prefix}_sd": statistics.pstdev(v),
            f"{prefix}_min": min(v), f"{prefix}_max": max(v)}


class Sentinel2Phenology(Source):
    """ดัชนีพืชพรรณ (NDVI) รายเดือนของปี — ตัดเมฆด้วย SCL · สรุปเป็นค่าเฉลี่ย/แกว่ง/ต่ำสุด/สูงสุด (รอบการเติบโต)"""
    name = "s2"
    names = ["ndvi_mean", "ndvi_sd", "ndvi_min", "ndvi_max"]

    def image(self, ee, geom, year):
        def ndvi(img):
            scl = img.select("SCL")
            ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
            return (img.normalizedDifference(["B8", "B4"]).rename("ndvi").updateMask(ok)
                    .set("system:time_start", img.get("system:time_start")))   # ต้องมีเวลาไว้กรองรายเดือน
        col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(geom)
               .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
               .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60)).map(ndvi))
        return ee.Image.cat([col.filter(ee.Filter.calendarRange(m, m, "month")).median().rename(f"ndvi_m{m:02d}")
                             for m in MONTHS])

    def to_features(self, raw):
        return _stats("ndvi", [raw.get(f"ndvi_m{m:02d}") for m in MONTHS])


class Sentinel1Seasonal(Source):
    """เรดาร์ VV/VH รายเดือน (ทะลุเมฆ สำคัญในหน้าฝน) · สรุปเป็นค่าเฉลี่ย/แกว่ง + สัดส่วน VH−VV"""
    name = "s1"
    names = ["vv_mean", "vv_sd", "vh_mean", "vh_sd", "vh_vv_mean"]

    def image(self, ee, geom, year):
        col = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(geom)
               .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
               .filter(ee.Filter.eq("instrumentMode", "IW"))
               .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
               .select(["VV", "VH"]))
        bands = []
        for m in MONTHS:
            mm = col.filter(ee.Filter.calendarRange(m, m, "month")).mean()
            bands += [mm.select("VV").rename(f"vv_m{m:02d}"), mm.select("VH").rename(f"vh_m{m:02d}")]
        return ee.Image.cat(bands)

    def to_features(self, raw):
        vv = [raw.get(f"vv_m{m:02d}") for m in MONTHS]
        vh = [raw.get(f"vh_m{m:02d}") for m in MONTHS]
        a, b = _stats("vv", vv), _stats("vh", vh)
        diff = [h - v for h, v in zip(vh, vv) if h is not None and v is not None]
        return {"vv_mean": a["vv_mean"], "vv_sd": a["vv_sd"], "vh_mean": b["vh_mean"], "vh_sd": b["vh_sd"],
                "vh_vv_mean": statistics.fmean(diff) if len(diff) >= 3 else None}


class DynamicWorldCover(Source):
    """สัดส่วนต้นไม้/พืชไร่/สิ่งปลูกสร้างในขอบเขตของปี (Dynamic World, CC BY 4.0) — ใช้ทั้งเป็น feature และด่าน "เป็นสวนไหม" """
    name = "dw"
    names = ["dw_trees", "dw_crops", "dw_built"]

    def image(self, ee, geom, year):
        lab = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(geom)
               .filterDate(f"{year}-01-01", f"{year + 1}-01-01").select("label").mode())
        return ee.Image.cat([lab.eq(1).rename("dw_trees"), lab.eq(4).rename("dw_crops"), lab.eq(6).rename("dw_built")])


FEATURE_SETS: dict[str, list[Source]] = {
    "emb_phen_v1": [SatelliteEmbedding(), Sentinel2Phenology(), Sentinel1Seasonal(), DynamicWorldCover()],
}
DEFAULT_FEATURE_SET = "emb_phen_v1"


def feature_names(feature_set: str = DEFAULT_FEATURE_SET) -> list[str]:
    return [n for s in FEATURE_SETS[feature_set] for n in s.names]
