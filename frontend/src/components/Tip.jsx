import { cloneElement, useCallback, useRef, useState } from "react";

/**
 * Hover/focus tooltip, always below its trigger.
 *
 * Below rather than above because the most-hovered controls are the topbar
 * chips, and there is no room over those: the header sits against the top
 * of the viewport, so an above-placement bubble renders half off-screen.
 *
 * Positioned `fixed` against the trigger's measured rect rather than
 * absolutely inside it, because the settings body and the queue list both
 * scroll, and an absolutely positioned bubble gets clipped by their overflow.
 *
 * Renders no wrapper element: it clones the child and attaches handlers, so
 * dropping a Tip around a flex item doesn't change the layout.
 */
export default function Tip({ text, children }) {
  const [box, setBox] = useState(null);
  const ref = useRef(null);

  const show = useCallback(() => {
    const el = ref.current;
    if (!el || !text) return;
    const r = el.getBoundingClientRect();
    setBox({
      // Clamped so a tooltip on a control near either edge stays on screen.
      // Half the max width plus the margin, which is what keeps the Support
      // chip's tip, the rightmost control on the page, fully visible.
      left: Math.min(Math.max(r.left + r.width / 2, 140), window.innerWidth - 140),
      top: r.bottom + 8,
    });
  }, [text]);

  const hide = useCallback(() => setBox(null), []);

  const child = cloneElement(children, {
    ref,
    onMouseEnter: show,
    onMouseLeave: hide,
    onFocus: show,
    onBlur: hide,
  });

  return (
    <>
      {child}
      {box && (
        <span role="tooltip" className="tip" style={{ left: box.left, top: box.top }}>
          {text}
        </span>
      )}
    </>
  );
}
