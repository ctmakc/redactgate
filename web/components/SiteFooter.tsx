export function SiteFooter() {
  return (
    <footer className="mt-10 font-mono text-micro text-graphite">
      <div className="perimeter flex items-center">raw values never cross this line</div>
      {/* Agency credit — house standard: wording + MMIX logo on a light chip. */}
      <div className="flex justify-center px-6 pt-5">
        <a
          href="https://mmix.ua/"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2.5 text-[12.5px] opacity-70 transition-opacity hover:opacity-100"
        >
          Development & promotion —
          <span className="inline-flex items-center rounded-md bg-white/90 px-2 py-1">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/mmix-logo.png" alt="MMIX — Marketing Mix" width={62} height={60} className="h-9 w-auto" />
          </span>
        </a>
      </div>
    </footer>
  );
}
