'use client';

import { AiFillGithub, AiFillInstagram } from "react-icons/ai";
import { FaLinkedinIn } from "react-icons/fa";
import Typewriter from "typewriter-effect";
import clsx from "clsx";
import ProgressCard from "@/components/home/ProgressCard";
import CodeCard from "@/components/home/CodeCard";
import { ScrollImage, StickyText } from "@/components/home/Gallery";
import FadeIn from "@/components/effects/FadeIn";
import AnimatedUnderline from "@/components/effects/AnimatedUnderline";

export default function Home() {
    // Shared social icon styles
    const socialIconClass = clsx(
        "relative inline-flex items-center justify-center w-10 h-10 text-[1.2em]",
        "bg-white text-black rounded-full",
        "transition-all duration-500 hover:text-primary",
        "before:content-[''] before:absolute before:top-0 before:left-0",
        "before:w-full before:h-full before:rounded-full",
        "before:transition-all before:duration-500 before:-z-10",
        "before:scale-[1.15] before:animate-[neonBluePurpleGradient_3s_infinite]"
    );

    return (
        <section>
            {/* Home Section */}
            <div id="home" className="w-full text-left text-[whitesmoke] m-0 p-0">
                <div className="relative h-screen w-full m-0 p-0 overflow-hidden">
                    <video
                        className="absolute top-0 left-0 w-full h-full object-cover -z-10 pointer-events-none"
                        src="/home/bg.mp4"
                        autoPlay
                        muted
                        loop
                        playsInline
                    />
                    <div className="w-full h-full px-[8vw] pb-[15vh] flex items-center">
                        <div className="w-full md:w-1/2 lg:w-5/12 xl:w-5/12">
                            <FadeIn>
                                <h1 className={clsx(
                                    "font-light mb-0 flex items-center",
                                    "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:0ms]",
                                    "after:content-[''] after:w-20 after:h-1 after:bg-primary after:ml-5"
                                )}>
                                    MAKE IT
                                </h1>
                            </FadeIn>
                            <FadeIn>
                                <h1 className={clsx(
                                    "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:100ms]"
                                )}>
                                    <Typewriter
                                        options={{
                                            strings: [
                                                "INNOVATIVE",
                                                "IMPACTFUL",
                                            ],
                                            cursor: '|',
                                            autoStart: true,
                                            loop: true,
                                            deleteSpeed: 80,
                                        }}
                                    />
                                </h1>
                            </FadeIn>
                            <FadeIn>
                                <h5 className={clsx(
                                    "ml-1 mt-2 font-semibold",
                                    "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:500ms]"
                                )}>
                                    I&apos;m Steven, a Master&apos;s student at the University of Pennsylvania, specializing in Machine Learning, Software Engineering and UI/UX Design.
                                </h5>
                            </FadeIn>
                        </div>
                    </div>
                </div>

                {/* About Section */}
                <div id="about" className="w-full px-[8vw] mt-[15vh]">
                    <div className="flex flex-wrap">
                        <div className="w-full xl:w-2/3">
                            <FadeIn>
                                <AnimatedUnderline className="mb-4 inline-block">
                                    <h2 className="mb-0">WHO AM I</h2>
                                </AnimatedUnderline>
                            </FadeIn>

                            <FadeIn>
                                <p className="mt-8">
                                    I&apos;m Hongyuan (Steven) Liu. I currently work full-time as a{" "}
                                    <i><b className="text-primary">Software Engineer in AI at Summation</b></i>,
                                    while pursuing a part-time{" "}<i><b className="text-primary">Master of Engineering
                                        in Artificial Intelligence</b></i>{" "}
                                    at the{" "} <i> <b className="text-primary"> University of Pennsylvania</b></i>.
                                    I graduated from the{" "}<i><b className="text-primary">University of Toronto</b></i>{" "}
                                    in June 2025 with a{" "}<i><b className="text-primary">BSc in Computer Science</b></i>{" "}
                                    (GPA 3.9/4.0). Previously, I worked as a{" "}<i> <b className="text-primary">Full-Stack Developer
                                        at Johnson Controls</b></i>{" "}and served as the{" "}<i> <b className="text-primary">Founder of
                                            Campus Eats</b></i>, where I led real-world software projects from concept to deployment.
                                </p>
                            </FadeIn>

                            <FadeIn>
                                <p className="mt-4">I am expertise in
                                    <i><b className="text-primary"> Machine Learning, Full-Stack AI Software Development, and UI/UX Design. </b></i>
                                    Besides, I am actively contributing to a diverse range of 10+ projects, software
                                    business startups, over 1000+ contribution on github.
                                </p>
                            </FadeIn>
                        </div>
                        <div className="w-full xl:w-1/3 px-4 flex justify-center items-center">
                            <video src='/home/avatar.mp4' autoPlay muted loop playsInline style={{ maxHeight: "400px" }} />
                        </div>
                    </div>
                </div>

                {/* What I Do Section */}
                <div className="w-full px-[8vw] py-[10vh]">
                    <div className="flex flex-wrap items-end">
                        <div className="w-full md:w-10/12 lg:w-2/3 xl:w-1/2">
                            <FadeIn>
                                <AnimatedUnderline className="mb-6 inline-block">
                                    <h2 className="mb-0">WHAT I DO</h2>
                                </AnimatedUnderline>
                            </FadeIn>
                            <FadeIn>
                                <h5 className="mb-6">
                                    I create engaging, interactive software powered by cutting-edge technologies.
                                </h5>
                            </FadeIn>
                            <FadeIn>
                                <ProgressCard />
                            </FadeIn>
                        </div>
                        <div className="w-full md:w-10/12 lg:w-2/3 xl:w-1/2">
                            <FadeIn>
                                <CodeCard />
                            </FadeIn>
                        </div>
                    </div>
                </div>

                {/* Gallery Section */}
                <StickyText text="There are many other things I do" />
                <div id="gallery" className="w-full px-[8vw] py-[10vh]">
                    <div className="relative mt-[5vh] pt-[45vh] pb-[10vh] px-0">
                        <div className="flex flex-wrap m-0 p-0">
                            <div id="first-image" className="w-full md:w-1/2 m-0 p-0 text-center">
                                <ScrollImage src="/home/lake.JPG" speed={1} z={0} />
                                <p className="w-[70%] mx-auto my-0.5 mt-[15%] mb-[15%]">
                                    Photography is one of my creative outlets — here&apos;s a glimpse into my gallery.
                                </p>
                            </div>
                            <div className="w-full md:w-1/2 m-0 p-0">
                                <ScrollImage src="/home/winnie.JPG" speed={0.8} wrapperWidth="70%"
                                    initialOffset={-20} />
                            </div>
                        </div>
                        <div className="flex flex-wrap m-0 p-0">
                            <div className="w-full md:w-1/2 m-0 p-0">
                                <ScrollImage src="/home/light.JPG" speed={1.1} initialOffset={10} />
                            </div>
                        </div>
                    </div>
                </div>

                {/* Contact Section */}
                <div
                    id="contact-section"
                    className={clsx(
                        "w-full mb-[10vh]",
                        "bg-[url('/home/get-in-touch.png')] bg-no-repeat bg-bottom-right bg-contain",
                        "max-xl:bg-[linear-gradient(rgba(0,0,0,0.7),rgba(0,0,0,0.7)),url('/home/get-in-touch.png')]"
                    )}
                >
                    <div className="w-full px-[8vw] py-[10vh]">
                        <div className="flex flex-wrap -mx-4">
                            <div className="w-full xl:w-2/3 px-4">
                                <FadeIn>
                                    <AnimatedUnderline className="mb-4 inline-block">
                                        <h2 className="mb-0">CONTACT ME AT</h2>
                                    </AnimatedUnderline>
                                </FadeIn>

                                <div id="contact" className="w-full p-0">
                                    <FadeIn>
                                        <h4 className="mt-4">
                                            <strong className="text-primary">Location:</strong> Bellevue WA, Toronto ON
                                        </h4>
                                    </FadeIn>
                                    <FadeIn>
                                        <h4 className="mt-3">
                                            <strong className="text-primary">Email:</strong> liuhongyuan2001 AT
                                            gmail.com
                                        </h4>
                                    </FadeIn>
                                    <FadeIn>
                                        <h4 className="mt-3">
                                            <strong className="text-primary">Phone:</strong> {"+1 (647)-309-9649"}
                                        </h4>
                                    </FadeIn>

                                    <FadeIn>
                                        <ul className="flex text-center p-0 text-white justify-start list-none mb-25 mt-5">
                                            <li className="me-4">
                                                <a
                                                    href="https://github.com/Yuanxyyds"
                                                    target="_blank"
                                                    rel="noreferrer"
                                                    className={socialIconClass}
                                                >
                                                    <AiFillGithub />
                                                </a>
                                            </li>
                                            <li className="me-4">
                                                <a
                                                    href="https://www.linkedin.com/in/liustev6/"
                                                    target="_blank"
                                                    rel="noreferrer"
                                                    className={socialIconClass}
                                                >
                                                    <FaLinkedinIn />
                                                </a>
                                            </li>
                                            <li className="me-4">
                                                <a
                                                    href="https://www.instagram.com/yuanxyyds/"
                                                    target="_blank"
                                                    rel="noreferrer"
                                                    className={socialIconClass}
                                                >
                                                    <AiFillInstagram />
                                                </a>
                                            </li>
                                        </ul>
                                    </FadeIn>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

        </section >
    );
}
