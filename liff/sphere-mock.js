/**
 * sphere-mock.js — Leaflet-backed drop-in replacement for GISTDA Sphere SDK.
 * Used when SPHERE_KEY is not configured (dev / demo mode).
 *
 * Full API parity with real sphere SDK:
 *   sphere.Map, sphere.Marker, sphere.Dot, sphere.Polygon,
 *   sphere.Polyline, sphere.Circle, sphere.Layers, sphere.EventName
 *
 * Real SDK: https://api.sphere.gistda.or.th/map/?key=KEY
 */
(function () {
  "use strict";

  /* ── helpers ── */
  const toLL = (loc) => [loc.lat, loc.lon || loc.lng];

  /* ── EventName enum (matches real Sphere SDK) ── */
  const EventName = {
    Ready:       "ready",
    Click:       "click",
    DoubleClick: "dblclick",
    Location:    "location",
    Zoom:        "zoom",
    Rotate:      "rotate",
  };

  /* ── Layers enum (matches real Sphere SDK) ── */
  const Layers = {
    SIMPLE:        "simple",
    STREETS:       "streets",
    STREETS_NIGHT: "streets_night",
    IMAGES:        "images",
    HYBRID:        "hybrid",
    NORMAL:        "normal",     // alias
  };

  /* ── Tile providers for Layers ── */
  const _tiles = {
    simple:        "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    streets:       "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    streets_night: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    images:        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    hybrid:        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    normal:        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  };

  /* ── Shared event mixin ── */
  function _eventMixin(obj) {
    obj._handlers = {};
    obj._on = function (evt, fn) {
      if (!this._handlers[evt]) this._handlers[evt] = [];
      this._handlers[evt].push(fn);
    };
    obj._fire = function (evt, data) {
      (this._handlers[evt] || []).forEach((fn) => fn(data));
    };
    obj.Event = {
      bind(eventName, fn) { obj._on(eventName, fn); }
    };
  }

  /* ════════════════════ sphere.Map ════════════════════════ */
  function SphereMap(opts) {
    const el     = opts.placeholder;
    const center = opts.location ? toLL(opts.location) : [12.6, 102.1];
    const zoom   = opts.zoom || 10;

    _eventMixin(this);

    this._leaflet = L.map(el, { zoomControl: true }).setView(center, zoom);

    // Base tile layer
    const layerKey = opts.layer || "hybrid";
    const tileUrl  = _tiles[layerKey] || _tiles.hybrid;
    this._baseLayer = L.tileLayer(tileUrl, {
      attribution: '&copy; <a href="https://sphere.gistda.or.th">GISTDA sphere (mock)</a>',
      maxZoom: 19,
    }).addTo(this._leaflet);

    // Hybrid label overlay (road names on top of satellite)
    if (layerKey === "hybrid") {
      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", {
        maxZoom: 19, pane: "overlayPane",
      }).addTo(this._leaflet);
    }

    const self = this;

    // Fire Ready after tick so bind() registered after constructor still catch it
    setTimeout(() => self._fire("ready"), 50);

    this._leaflet.on("click", (e) => {
      self._fire("click", { lat: e.latlng.lat, lon: e.latlng.lng });
    });
    this._leaflet.on("dblclick", (e) => {
      self._fire("dblclick", { lat: e.latlng.lat, lon: e.latlng.lng });
    });
    this._leaflet.on("moveend", () => self._fire("location"));
    this._leaflet.on("zoomend", () => self._fire("zoom"));

    /* ── Overlays sub-object ── */
    this.Overlays = {
      _map: this._leaflet,
      _items: [],
      add(overlay) {
        if (overlay && overlay._layer) {
          overlay._layer.addTo(this._map);
          this._items.push(overlay);
        }
      },
      remove(overlay) {
        if (overlay && overlay._layer) {
          try { this._map.removeLayer(overlay._layer); } catch (_) {}
          this._items = this._items.filter((o) => o !== overlay);
        }
      },
      clear() {
        this._items.forEach((o) => {
          try { this._map.removeLayer(o._layer); } catch (_) {}
        });
        this._items = [];
      },
    };

    /* ── Layers sub-object ── */
    this.Layers = {
      _map: this._leaflet,
      _baseRef: this._baseLayer,
      setBase(layerEnum) {
        const url = _tiles[layerEnum] || _tiles.hybrid;
        try { self._leaflet.removeLayer(self._baseLayer); } catch (_) {}
        self._baseLayer = L.tileLayer(url, {
          attribution: '&copy; <a href="https://sphere.gistda.or.th">GISTDA sphere (mock)</a>',
          maxZoom: 19,
        }).addTo(self._leaflet);
      },
    };
  }

  /** Pan to location.  sphere API: map.location({lon, lat}, animate) */
  SphereMap.prototype.location = function (loc, animate) {
    if (loc) {
      this._leaflet.setView(toLL(loc), this._leaflet.getZoom(), { animate: !!animate });
    }
    return {
      lat: this._leaflet.getCenter().lat,
      lon: this._leaflet.getCenter().lng,
    };
  };

  /** Set/get zoom level.  sphere API: map.zoom(level) */
  SphereMap.prototype.zoom = function (level, animate) {
    if (level !== undefined) {
      this._leaflet.setZoom(level, { animate: !!animate });
    }
    return this._leaflet.getZoom();
  };

  /** Combined pan + zoom.  sphere API: map.goTo({center, zoom}) */
  SphereMap.prototype.goTo = function (opts) {
    const center = opts.center ? toLL(opts.center) : undefined;
    const zoom   = opts.zoom;
    if (center && zoom) {
      this._leaflet.setView(center, zoom, { animate: true });
    } else if (center) {
      this._leaflet.setView(center, this._leaflet.getZoom(), { animate: true });
    } else if (zoom) {
      this._leaflet.setZoom(zoom, { animate: true });
    }
  };

  /** Get bounding box.  sphere API: map.bound() */
  SphereMap.prototype.bound = function () {
    const b = this._leaflet.getBounds();
    return {
      minLat: b.getSouth(), minLon: b.getWest(),
      maxLat: b.getNorth(), maxLon: b.getEast(),
    };
  };

  /** Invalidate size after container resize */
  SphereMap.prototype.resize = function () {
    this._leaflet.invalidateSize();
  };

  /* ════════════════════ sphere.Marker ═════════════════════ */
  function SphereMarker(loc, opts) {
    opts = opts || {};
    _eventMixin(this);

    this._latlng = toLL(loc);

    // Custom icon or default pin
    let icon;
    if (opts.icon && opts.icon.html) {
      icon = L.divIcon({
        html: opts.icon.html,
        className: "sphere-marker-html",
        iconAnchor: opts.icon.offset ? [opts.icon.offset.x, opts.icon.offset.y] : [12, 41],
      });
    } else if (opts.icon && opts.icon.url) {
      icon = L.icon({
        iconUrl: opts.icon.url,
        iconAnchor: opts.icon.offset ? [opts.icon.offset.x, opts.icon.offset.y] : [12, 41],
      });
    } else {
      // Default pin icon (SVG data URI — green pin)
      const color = opts.color || "#1a7a3c";
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="25" height="41" viewBox="0 0 25 41">
        <path d="M12.5 0C5.6 0 0 5.6 0 12.5c0 2.4.7 4.7 1.9 6.6L12.5 41l10.6-21.9c1.2-1.9 1.9-4.2 1.9-6.6C25 5.6 19.4 0 12.5 0z" fill="${color}"/>
        <circle cx="12.5" cy="12.5" r="5" fill="white"/>
      </svg>`;
      icon = L.divIcon({
        html: svg,
        className: "sphere-marker-pin",
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [0, -35],
      });
    }

    this._layer = L.marker(this._latlng, {
      icon: icon,
      draggable: !!opts.draggable,
      title: opts.title || "",
    });

    // Popup
    if (opts.popup && opts.popup.html) {
      this._layer.bindPopup(opts.popup.html);
    } else if (opts.detail) {
      this._layer.bindPopup(`<b>${opts.title || ""}</b><br>${opts.detail}`);
    }

    const self = this;
    this._layer.on("click", () => self._fire("click"));
    this._layer.on("drag", () => self._fire("drag"));
  }

  SphereMarker.prototype.location = function (loc) {
    if (loc) {
      this._latlng = toLL(loc);
      if (this._layer) this._layer.setLatLng(this._latlng);
    }
    return { lat: this._latlng[0], lon: this._latlng[1] };
  };

  /* ════════════════════ sphere.Dot ════════════════════════ */
  function Dot(loc, opts) {
    opts = opts || {};
    _eventMixin(this);

    const radius = Math.max(4, (opts.lineWidth || 10) / 2);
    const color  = opts.lineColor || "#1a7a3c";
    this._latlng = toLL(loc);
    this._layer  = L.circleMarker(this._latlng, {
      radius, color, fillColor: color, fillOpacity: 0.85, weight: 2,
    });

    const self = this;
    this._layer.on("click", () => self._fire("click"));
  }

  Dot.prototype.location = function (loc) {
    if (loc) {
      this._latlng = toLL(loc);
      if (this._layer) this._layer.setLatLng(this._latlng);
    }
    return { lat: this._latlng[0], lon: this._latlng[1] };
  };

  /* ════════════════════ sphere.Polygon ════════════════════ */
  function SpherePolygon(locs, opts) {
    opts = opts || {};
    _eventMixin(this);
    const coords = locs.filter(Boolean).map(toLL);
    this._layer = L.polygon(coords, {
      color:       opts.lineColor   || "#06C755",
      weight:      opts.lineWidth   || 2,
      fillColor:   opts.fillColor   || "rgba(6,199,85,0.2)",
      fillOpacity: opts.fillOpacity != null ? opts.fillOpacity : 0.3,
    });
    const self = this;
    this._layer.on("click", () => self._fire("click"));
  }

  SpherePolygon.prototype.size = function () {
    // Approximate area in sq meters using Leaflet's geodesicArea
    if (this._layer && typeof L.GeometryUtil !== "undefined") {
      return L.GeometryUtil.geodesicArea(this._layer.getLatLngs()[0]);
    }
    return 0;
  };

  /* ════════════════════ sphere.Polyline ═══════════════════ */
  function SpherePolyline(locs, opts) {
    opts = opts || {};
    _eventMixin(this);
    const coords = locs.filter(Boolean).map(toLL);
    this._layer = L.polyline(coords, {
      color:  opts.lineColor || "#06C755",
      weight: opts.lineWidth || 2,
    });
    const self = this;
    this._layer.on("click", () => self._fire("click"));
  }

  /* ════════════════════ sphere.Circle ═════════════════════ */
  function SphereCircle(loc, radius, opts) {
    opts = opts || {};
    _eventMixin(this);
    this._layer = L.circle(toLL(loc), {
      radius:      radius * 111320,  // degrees → meters (approx)
      color:       opts.lineColor   || "#06C755",
      weight:      opts.lineWidth   || 2,
      fillColor:   opts.fillColor   || "rgba(6,199,85,0.2)",
      fillOpacity: 0.3,
    });
  }

  /* ── Export as window.sphere ── */
  window.sphere = {
    Map:       SphereMap,
    Marker:    SphereMarker,
    Dot:       Dot,
    Polygon:   SpherePolygon,
    Polyline:  SpherePolyline,
    Circle:    SphereCircle,
    Layers:    Layers,
    EventName: EventName,
  };
})();
