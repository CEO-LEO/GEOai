"""Quick test for combined weather + soil alert logic"""
from weather_alert import assess_soil_waterlog_risk, evaluate_combined_risk

# ─── Scenario 1: CRITICAL — basin + subsiding + heavy rain ───
print("=" * 50)
print("Scenario 1: CRITICAL — bad soil + heavy rain")
print("=" * 50)

soil = assess_soil_waterlog_risk({
    "elevation_diff": -2.0,
    "surface_stability": 0.25,
    "displacement_vv_change": 3.5,
    "soil_moisture_vv": -9.0,
    "displacement_level": "high",
    "land_impact_severity": "high",
})
print(f"Soil: basin={soil['is_basin']}, subsiding={soil['is_subsiding']}, "
      f"unstable={soil['is_unstable']}, waterlogged={soil['is_waterlogged']}")
print(f"Soil risk score: {soil['soil_risk_score']}")
for f in soil["risk_factors"]:
    print(f"  - {f}")

forecast_heavy = {
    "total_rain_mm": 120.0,
    "max_daily_mm": 55.0,
    "rainy_days": 5,
    "is_heavy_rain": True,
}
alert = evaluate_combined_risk(forecast_heavy, soil)
print(f"\nAlert: level={alert['alert_level']}, score={alert['combined_score']}")
print(f"Title: {alert['alert_title']}")
print(f"Waterlog: {alert['waterlog_risk']}, Stop fertilizer: {alert['stop_fertilizer']}")
for a in alert["advisories"]:
    print(f"  {a}")
assert alert["alert_level"] == "critical", f"Expected critical, got {alert['alert_level']}"
assert alert["waterlog_risk"] is True
assert alert["stop_fertilizer"] is True

# ─── Scenario 2: WARNING — moderate rain + bad soil ───
print("\n" + "=" * 50)
print("Scenario 2: WARNING — moderate rain + bad soil")
print("=" * 50)

forecast_mod = {
    "total_rain_mm": 60.0,
    "max_daily_mm": 20.0,
    "rainy_days": 3,
    "is_heavy_rain": False,
}
alert2 = evaluate_combined_risk(forecast_mod, soil)
print(f"Alert: level={alert2['alert_level']}, score={alert2['combined_score']}")
print(f"Title: {alert2['alert_title']}")
print(f"Waterlog: {alert2['waterlog_risk']}, Stop fertilizer: {alert2['stop_fertilizer']}")
for a in alert2["advisories"]:
    print(f"  {a}")
assert alert2["alert_level"] == "warning"
assert alert2["should_notify"] is True

# ─── Scenario 3: WARNING — heavy rain + good soil ───
print("\n" + "=" * 50)
print("Scenario 3: WARNING — heavy rain + good soil")
print("=" * 50)

good_soil = assess_soil_waterlog_risk({
    "elevation_diff": 0.5,
    "surface_stability": 0.8,
    "displacement_vv_change": 0.3,
    "soil_moisture_vv": -15,
    "displacement_level": "low",
    "land_impact_severity": "low",
})
print(f"Soil: score={good_soil['soil_risk_score']}, factors={good_soil['risk_factors']}")
alert3 = evaluate_combined_risk(forecast_heavy, good_soil)
print(f"Alert: level={alert3['alert_level']}, score={alert3['combined_score']}")
print(f"Stop fertilizer: {alert3['stop_fertilizer']}")
for a in alert3["advisories"]:
    print(f"  {a}")
assert alert3["alert_level"] == "warning"
assert alert3["stop_fertilizer"] is True

# ─── Scenario 4: WATCH — no rain + soil critical ───
print("\n" + "=" * 50)
print("Scenario 4: WATCH — no rain + critical soil")
print("=" * 50)

forecast_dry = {
    "total_rain_mm": 10.0,
    "max_daily_mm": 5.0,
    "rainy_days": 1,
    "is_heavy_rain": False,
}
alert4 = evaluate_combined_risk(forecast_dry, soil)
print(f"Alert: level={alert4['alert_level']}, score={alert4['combined_score']}")
print(f"Title: {alert4['alert_title']}")
for a in alert4["advisories"]:
    print(f"  {a}")
assert alert4["alert_level"] == "watch"

# ─── Scenario 5: NONE — no rain + good soil ───
print("\n" + "=" * 50)
print("Scenario 5: NONE — no rain + good soil")
print("=" * 50)

alert5 = evaluate_combined_risk(forecast_dry, good_soil)
print(f"Alert: level={alert5['alert_level']}, should_notify={alert5['should_notify']}")
assert alert5["alert_level"] == "none"
assert alert5["should_notify"] is False

print("\n" + "=" * 50)
print("ALL 5 SCENARIOS PASSED ✅")
print("=" * 50)
