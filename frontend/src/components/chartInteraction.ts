// Shared cartesian ECharts interaction settings.
// Separate x/y inside dataZoom controllers keep both drag directions usable.
export function createCartesianDataZoom() {
  const common = {
    type: "inside" as const,
    filterMode: "none" as const,
    throttle: 60,
    zoomOnMouseWheel: true,
    moveOnMouseMove: true,
    moveOnMouseWheel: false,
    preventDefaultMouseMove: true,
    cursorGrab: "grab",
    cursorGrabbing: "grabbing",
  };

  return [
    { ...common, xAxisIndex: 0 },
    { ...common, yAxisIndex: 0 },
  ];
}
