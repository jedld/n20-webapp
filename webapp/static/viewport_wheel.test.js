const ZOOM_STEP = 0.1;

function resolveViewportWheelGesture(ctrlKey, deltaY) {
  if (ctrlKey) {
    return {
      action: 'zoom',
      zoomDelta: deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP,
    };
  }
  return { action: 'pan' };
}

describe('resolveViewportWheelGesture', () => {
  test('plain trackpad scroll pans', () => {
    expect(resolveViewportWheelGesture(false, 12)).toEqual({ action: 'pan' });
    expect(resolveViewportWheelGesture(false, -8)).toEqual({ action: 'pan' });
  });

  test('pinch gesture (ctrl+wheel) zooms', () => {
    expect(resolveViewportWheelGesture(true, 10)).toEqual({
      action: 'zoom',
      zoomDelta: -ZOOM_STEP,
    });
    expect(resolveViewportWheelGesture(true, -10)).toEqual({
      action: 'zoom',
      zoomDelta: ZOOM_STEP,
    });
  });
});
