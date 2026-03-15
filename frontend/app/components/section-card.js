export default function SectionCard({ eyebrow, title, meta, children, className = "" }) {
  const classes = ["section-card", className].filter(Boolean).join(" ");
  return (
    <section className={classes}>
      {(eyebrow || title || meta) ? (
        <div className="section-head">
          <div>
            {eyebrow ? <p className="section-eyebrow">{eyebrow}</p> : null}
            {title ? <h2 className="section-title">{title}</h2> : null}
          </div>
          {meta ? <span className="section-meta">{meta}</span> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
