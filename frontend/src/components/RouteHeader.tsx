type RouteHeaderProps = {
  eyebrow: string;
  title: string;
  description: string;
};

export function RouteHeader({ eyebrow, title, description }: RouteHeaderProps) {
  return (
    <header className="border-b border-line pb-5">
      <p className="label-caps text-ink-muted">{eyebrow}</p>
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,0.74fr)_minmax(18rem,0.26fr)] lg:items-end">
        <h1 className="font-display text-3xl font-bold leading-tight tracking-[-0.03em] text-ink md:text-4xl">
          {title}
        </h1>
        <p className="max-w-[54ch] text-sm leading-6 text-ink-muted md:text-base">{description}</p>
      </div>
    </header>
  );
}
