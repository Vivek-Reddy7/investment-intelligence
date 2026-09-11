import "./globals.css";
import Link from "next/link";

export const metadata = {
  title: "Point-in-time fundamentals screener",
  description:
    "Screen company fundamentals as of any past date, using only what was known then.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site">
          <div className="wrap">
            <h1><Link href="/" style={{ textDecoration: "none", color: "inherit" }}>
              Point-in-time fundamentals
            </Link></h1>
            <span className="sub">what a screen would have returned, using only what was known then</span>
          </div>
        </header>
        <div className="wrap">{children}</div>
      </body>
    </html>
  );
}
