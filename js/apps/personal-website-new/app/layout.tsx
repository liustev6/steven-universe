import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hongyuan (Steven) Liu | AI Developer",
  description: "Portfolio of Hongyuan (Steven) Liu, M.Eng. student at UPenn specializing in Machine Learning, Software Engineering, and UI/UX Design.",
  icons: {
    icon: "/favicon.png",
  },
  openGraph: {
    title: "Hongyuan (Steven) Liu | AI Developer",
    description: "Portfolio of Hongyuan (Steven) Liu, M.Eng. student at UPenn specializing in Machine Learning, Software Engineering, and UI/UX Design.",
    url: "https://www.liustev6.ca",
    siteName: "Hongyuan (Steven) Liu",
    images: [
      {
        url: "https://www.liustev6.ca/cover.png",
        width: 1200,
        height: 630,
        alt: "Hongyuan (Steven) Liu Portfolio",
      },
    ],
    locale: "en_US",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Hongyuan (Steven) Liu | AI Developer",
    description: "Hongyuan (Steven) Liu's personal portfolio. I'm Steven, a Master's student at the University of Pennsylvania, specializing in Machine Learning, Software Engineering and UI/UX Design.",
    images: ["https://www.liustev6.ca/cover.png"],
  },
  other: {
    "google-site-verification": "your-verification-code", // Update if you have one
  },
};

/**
 * The root layout: only the document. Each part of the site brings its own chrome:
 * - app/(site)/      the current website (loader, neon trail, navbar, footer, chatbot)
 * - app/prototype/   the new website's prototype, built from a clean page
 * A route group's folder name, "(site)", never appears in URLs.
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
