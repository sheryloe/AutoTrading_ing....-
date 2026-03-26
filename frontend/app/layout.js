import { IBM_Plex_Mono, Noto_Sans_KR, Space_Grotesk } from "next/font/google";
import AppShell from "./components/app-shell";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
});

const notoSansKr = Noto_Sans_KR({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "700"],
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
});

export const metadata = {
  title: "AI_Auto Trading Console",
  description: "Volatility-focused auto-trading dashboard for overview, model performance, positions, and runtime controls.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ko">
      <body className={`${spaceGrotesk.variable} ${notoSansKr.variable} ${ibmPlexMono.variable}`}>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
