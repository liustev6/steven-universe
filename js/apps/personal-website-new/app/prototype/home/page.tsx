/** /prototype/home: the new website's home page. A placeholder for now. */
export default function PrototypeHome() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <p className="text-sm uppercase tracking-[0.3em] text-primary-light">Prototype</p>
      <h1 className="text-4xl font-semibold sm:text-5xl">Home</h1>
      <p className="max-w-md text-white/60">The new personal website starts here. This page is a placeholder.</p>
    </main>
  );
}
