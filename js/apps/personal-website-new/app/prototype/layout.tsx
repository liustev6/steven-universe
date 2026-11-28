import type { Metadata } from "next";

/**
 * The new personal website's prototype, under /prototype: a clean page (none of the current
 * site's chrome), kept out of search engines while it's being built.
 */
export const metadata: Metadata = {
  title: "Prototype | Hongyuan (Steven) Liu",
  robots: { index: false, follow: false },
};

export default function PrototypeLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <div className="min-h-screen bg-background text-white">{children}</div>;
}
