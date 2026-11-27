// Deployment settings for the front end. Nothing here is secret.
window.ROADWORK_CONFIG = {
  apiBase: "",                                   // same origin by default
  tiles: "/tiles/{z}/{x}/{y}.pbf",
  tileMaxZoom: 13,
  // Background map with street labels. Any MapLibre style URL works. If it cannot be loaded,
  // the roads are still drawn on a plain background.
  basemapStyle: "https://tiles.openfreemap.org/styles/positron",
  center: [-110.97, 32.24],
  zoom: 11.2,
  bounds: [[-113.6, 31.2], [-110.2, 32.7]],      // Pima County, with a margin
};
