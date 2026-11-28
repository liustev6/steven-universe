import NavBar from "@/components/common/Navbar";
import Loader from "@/components/common/Loader";
import ChatbotWrapper from "@/components/common/ChatbotWrapper";
import NeonTrail from "@/components/effects/NeonTrail";
import FooterWrapper from "@/components/common/FooterWrapper";

/** The current website's chrome, around every page in this route group (URLs unchanged). */
export default function SiteLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <>
      <Loader />
      <NeonTrail />
      <NavBar />
      {children}
      <FooterWrapper />
      <ChatbotWrapper />
    </>
  );
}
