import { IBM_Plex_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
});

export const metadata = {
  title: "AI_Auto Dashboard",
  description: "Planner-based crypto execution dashboard powered by Supabase.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ko">
      <body className={`${spaceGrotesk.variable} ${ibmPlexMono.variable}`}>{children}</body>
    </html>
  );
}
