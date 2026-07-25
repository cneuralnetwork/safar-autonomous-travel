'use dom';

import { useEffect, useRef, useState } from 'react';
import type * as Leaflet from 'leaflet';
import type { ItineraryMapPoint } from '@/components/itineraryMapPoints';
import './leafletRouteMap.css';

interface LeafletRouteMapProps {
  points: ItineraryMapPoint[];
  expanded?: boolean;
  dom?: object;
}

function popupContent(point: ItineraryMapPoint, index: number): HTMLElement {
  const root = document.createElement('div');
  root.className = 'safar-popup';

  const eyebrow = document.createElement('div');
  eyebrow.className = 'safar-popup__eyebrow';
  eyebrow.textContent = `Stop ${index + 1} · ${point.category}`;

  const title = document.createElement('strong');
  title.className = 'safar-popup__title';
  title.textContent = point.title;

  const detail = document.createElement('div');
  detail.className = 'safar-popup__detail';
  const startsAt = new Date(point.startAt);
  const time = Number.isNaN(startsAt.getTime())
    ? ''
    : startsAt.toLocaleString([], {
        weekday: 'short',
        hour: 'numeric',
        minute: '2-digit',
      });
  detail.textContent = [time, point.location].filter(Boolean).join(' · ');

  root.append(eyebrow, title);
  if (detail.textContent) root.append(detail);
  return root;
}

export default function LeafletRouteMap({
  points,
  expanded = false,
}: LeafletRouteMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const leafletRef = useRef<typeof Leaflet | null>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);
  const routeLayerRef = useRef<Leaflet.LayerGroup | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    let cancelled = false;
    let resizeTimer: number | undefined;
    void import('leaflet').then((leaflet) => {
      if (cancelled || !containerRef.current) return;
      leafletRef.current = leaflet;
      const map = leaflet.map(containerRef.current, {
        attributionControl: true,
        zoomControl: expanded,
        scrollWheelZoom: expanded,
        dragging: true,
        touchZoom: true,
        doubleClickZoom: true,
      });
      leaflet.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
      }).addTo(map);
      routeLayerRef.current = leaflet.layerGroup().addTo(map);
      mapRef.current = map;
      resizeTimer = window.setTimeout(() => map.invalidateSize(), 80);
      setReady(true);
    });

    return () => {
      cancelled = true;
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      mapRef.current?.remove();
      leafletRef.current = null;
      mapRef.current = null;
      routeLayerRef.current = null;
      setReady(false);
    };
  }, [expanded]);

  useEffect(() => {
    const leaflet = leafletRef.current;
    const map = mapRef.current;
    const routeLayer = routeLayerRef.current;
    if (!ready || !leaflet || !map || !routeLayer || !points.length) return;

    routeLayer.clearLayers();
    const coordinates = points.map(
      (point) => [point.latitude, point.longitude] as Leaflet.LatLngTuple,
    );

    points.forEach((point, index) => {
      const icon = leaflet.divIcon({
        className: 'safar-marker-shell',
        html: `<span class="safar-marker">${index + 1}</span>`,
        iconAnchor: [15, 15],
        iconSize: [30, 30],
        popupAnchor: [0, -14],
      });
      leaflet.marker([point.latitude, point.longitude], {
        icon,
        keyboard: true,
        title: `${index + 1}. ${point.title}`,
      })
        .bindPopup(popupContent(point, index), {
          closeButton: false,
          offset: [0, -2],
        })
        .addTo(routeLayer);
    });

    if (coordinates.length > 1) {
      leaflet.polyline(coordinates, {
        color: '#5547E8',
        opacity: 0.86,
        weight: expanded ? 4 : 3,
        lineCap: 'round',
        lineJoin: 'round',
        dashArray: '2 9',
      }).addTo(routeLayer);
    }

    if (coordinates.length === 1) {
      map.setView(coordinates[0]!, 14);
    } else {
      map.fitBounds(leaflet.latLngBounds(coordinates), {
        animate: false,
        padding: expanded ? [42, 42] : [24, 24],
        maxZoom: 15,
      });
    }
    window.setTimeout(() => map.invalidateSize(), 0);
  }, [expanded, points, ready]);

  return (
    <div
      ref={containerRef}
      className={`safar-leaflet-map${expanded ? ' safar-leaflet-map--expanded' : ''}`}
      aria-label={`Interactive itinerary map with ${points.length} real locations`}
    />
  );
}
