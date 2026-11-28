'use client';

import { useEffect, useState, useCallback } from "react";
import { ScrollImage, StickyText } from "@/components/home/Gallery";
import { useVideoPopup } from "@/components/project/PopupVideoPlayer";
import { useResizeManager } from "@/hooks/useResizeManager";
import clsx from "clsx";
import FadeIn from "@/components/effects/FadeIn";
import AnimatedUnderline from "@/components/effects/AnimatedUnderline";
import { YouTubePlayer } from "@/components/project/YouTubePlayer";

// Dynamic import to prevent SSR issues

const XS_BREAKPOINT = 767;

/**
 * Campus Eats project showcase page
 *
 * Features:
 * - Responsive hero section with background images
 * - About Us section with embedded YouTube video
 * - Our Journey timeline with multiple videos
 * - Interactive scroll-based image gallery with parallax effects
 * - Animated section underlines that grow on scroll
 * - Video popup modal for detailed demos
 */
export default function CampusEats() {
    const [isVertical, setIsVertical] = useState(false);
    const [isXS, setIsXS] = useState(false);
    const { VideoPopup, openVideo } = useVideoPopup();

    const checkVertical = useCallback(() => {
        setIsVertical(window.innerWidth < 992);
    }, []);

    const checkXS = useCallback(() => {
        setIsXS(window.innerWidth < XS_BREAKPOINT);
    }, []);

    useResizeManager(checkVertical);
    useResizeManager(checkXS);

    useEffect(() => {
        checkVertical();
        checkXS();
    }, [checkVertical, checkXS]);


    return (
        <section>
            <VideoPopup />

            {/* Hero Section */}
            <div className="relative h-screen min-h-137.5 sm:min-h-150 md:min-h-195 m-0 p-0 overflow-hidden text-white bg-black/40">
                {isVertical ? (
                    <img
                        className="absolute top-0 left-0 w-full h-full pointer-events-none -z-10"
                        style={{ objectFit: 'fill' }}
                        src="/project-demo/campus-eats-bg-vertical.jpg"
                        alt="campus-eats-bg-cropped"
                    />
                ) : (
                    <img
                        className="absolute top-0 left-0 w-full h-full pointer-events-none -z-10"
                        style={{ objectFit: 'fill' }}
                        src="/project-demo/campus-eats-bg.jpg"
                        alt="campus-eats-bg"
                    />
                )}

                <div className="w-full h-full px-[8vw] pt-[max(100px,15vh)] pb-[max(100px,15vh)] sm:pt-[max(60px,15vh)] sm:pb-[max(60px,15vh)] flex">
                    <div className="flex-1"></div>
                    <div className="w-full sm:w-10/12 md:w-8/12 lg:w-7/12 xl:w-1/2 h-[70vh] min-h-112.5 sm:min-h-120 md:min-h-145">
                        <div className="flex flex-col items-center justify-evenly w-full h-full p-[8%_10%] opacity-90" style={{ backgroundColor: '#ffe62c' }}>
                            <img
                                src="/project/campus-eats.png"
                                className={clsx(
                                    "w-full",
                                    "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:300ms]"
                                )}
                                alt="campus-eats"
                            />
                            <div className="flex flex-col items-center">
                                <h2 className={clsx(
                                    "mb-2 font-bold",
                                    "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:500ms]"
                                )} style={{ color: '#ff621f' }}>
                                    Campus Eats
                                </h2>
                                <p className={clsx(
                                    "mb-0 font-semibold text-center w-[90%] text-white",
                                    "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:500ms]"
                                )}>
                                    Bringing your favorite off-campus meals to you - affordably, efficiently, and right where you are
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* About Us Section */}
            <div className="px-[8vw] py-[15vh] text-white" id="about-section">
                <FadeIn>
                    <AnimatedUnderline className="inline-block text-left">
                        <h2 id="about" className="mb-0">About Us</h2>
                    </AnimatedUnderline>
                </FadeIn>

                <div className="flex flex-wrap mt-5">
                    <div className="w-full xl:w-2/3 pr-0 xl:pr-8">
                        <FadeIn>
                            <p className="mt-5">
                                Campus Eats is a student-led startup on a mission to <i><strong className="text-primary"> transform the campus dining experience </strong></i> through
                                an all-in-one mobile platform. Beyond our core <i><strong className="text-primary"> Grab and Go central delivery system,</strong></i>
                                we offer <i><strong className="text-primary"> dine-in student coupons, food truck online ordering,</strong></i>
                                and a <i><strong className="text-primary"> dynamic food options exploration map</strong></i> to help students discover meals across campus.
                            </p>
                        </FadeIn>

                        <FadeIn>
                            <p className="mt-4">
                                We address the limitations of in-person-only ordering and the high fees of traditional delivery apps by offering a
                                streamlined, affordable alternative. Through Campus Eats, students can browse menus, order ahead, skip lines,
                                and access exclusive discounts—making campus dining more diverse, accessible, and student-friendly than ever.
                            </p>
                        </FadeIn>
                    </div>

                    <div className="w-full md:w-10/12 lg:w-9/12 xl:w-1/3 mt-5 xl:mt-0">
                        <div className="relative w-full aspect-video">
                            <YouTubePlayer url="https://youtu.be/nSsBSv1q4Bc" />
                        </div>
                    </div>
                </div>
            </div>

            {/* Our Journey Section */}
            <div className="px-[8vw] pb-[50vh] text-white" id="timeline-section">
                <FadeIn>
                    <AnimatedUnderline className="inline-block text-left">
                        <h2 id="timeline" className="mb-0">Our Journey</h2>
                    </AnimatedUnderline>
                </FadeIn>

                <div className="flex flex-wrap m-0 p-0">
                    <div className="w-full md:w-10/12 lg:w-9/12 xl:w-1/2 p-2 mt-5">
                        <div className="relative w-full aspect-video">
                            <YouTubePlayer url="https://youtu.be/-iV_XxbnngA" />
                        </div>
                    </div>
                    <div className="w-full md:w-10/12 lg:w-9/12 xl:w-1/2 p-2 mt-5">
                        <div className="relative w-full aspect-video">
                            <YouTubePlayer url="https://youtu.be/VVPSaiIJwNQ" />
                        </div>
                    </div>
                </div>
            </div>

            {/* Timeline with ScrollImages */}
            <StickyText text="Explore our journey from idea" targetId="timeline" fadeEndId="first-image" />

            <div className="px-[8vw] py-[15vh] text-white">
                <div className="text-center">
                    {/* Row 1: Starting & Nov 2021 */}
                    <div className="flex flex-wrap m-0 p-0">
                        <div className="w-full md:w-1/2 m-0 p-0" id="first-image">
                            <ScrollImage
                                src="/project-demo/command-line.png"
                                wrapperWidth="90%"
                                speed={1}
                                z={0}
                                tag="Starting"
                                tagLocation="20%"
                                overlayText="Watch Command Line Video Demo"
                                overlayAction={() => openVideo('https://youtu.be/J3kfU4Ic8Uc')}
                            />
                            <p className="md w-4/5 mx-auto my-2" style={{
                                marginTop: isXS ? '8vh' : '12vh',
                                marginBottom: isXS ? '8vh' : '12vh'
                            }}>
                                Campus Eats began as Group Asoul's final project for CSC207—a Java-based food truck ordering system with both backend logic and a CLI/Android frontend. This marked the first step toward building today's Campus Eats platform.
                            </p>
                        </div>
                        <div className="w-full md:w-1/2 m-0 p-0">
                            <ScrollImage
                                src="/project-demo/asoul.png"
                                speed={0.8}
                                wrapperWidth="60%"
                                tag="Nov 2021"
                                tagLocation="20%"
                                initialOffset={-20}
                                overlayText="Watch Asoul Android App Demo"
                                overlayAction={() => openVideo('https://youtu.be/RT-l98ZasE4')}
                            />
                        </div>
                    </div>

                    {/* Row 2: May 2023 */}
                    <div className="flex flex-wrap m-0 p-0">
                        <div className="w-full md:w-1/2 m-0 p-0">
                            {isXS && (
                                <p className="md w-4/5 mx-auto my-8">
                                    After its launch by Group Asoul, Campus Eats entered its second phase with a new team, re-integrating by Flutter and Firebase, featuring separate user and seller apps for a richer, modernized experience.
                                </p>
                            )}
                            <ScrollImage
                                src="/project-demo/campus-eats-1.png"
                                speed={1}
                                wrapperWidth="60%"
                                initialOffset={20}
                                tag="May 2023"
                                tagLocation="15%"
                                overlayText="Watch Campus Eats V1 Demo"
                                overlayAction={() => openVideo('https://youtu.be/0pP6WPmZV9M')}
                            />
                        </div>
                        <div className={clsx(
                            "w-full md:w-1/2 flex flex-col",
                            isXS ? "justify-start" : "justify-end pb-15"
                        )}>
                            {!isXS && (
                                <p className="md w-4/5 mx-auto my-16">
                                    After its launch by Group Asoul, Campus Eats entered its second phase with a new team, re-integrating by Flutter and Firebase, featuring separate user and seller apps for a richer, modernized experience.
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Row 3: Pitch & Team */}
                    <div className="flex flex-wrap p-0">
                        <div className="w-full md:w-1/2 m-0 p-0">
                            <p className="md w-4/5 mx-auto my-2" style={{
                                marginTop: isXS ? '8vh' : '5vh',
                                marginBottom: isXS ? '8vh' : '13vh'
                            }}>
                                Here are some snapshots of Campus Eats—a project we not only built and developed technically, but also advanced through business development and pitch presentations.
                            </p>
                            <ScrollImage
                                src="/project-demo/campus-eats-steven.jpg"
                                wrapperWidth="70%"
                                speed={1}
                                z={0}
                                tag="Pitch"
                                initialOffset={50}
                                overlayText="Watch our Hult Prize Pitch"
                                overlayAction={() => openVideo('https://youtu.be/VVPSaiIJwNQ')}
                            />
                        </div>
                        <div className="w-full md:w-1/2 m-0 p-0">
                            <ScrollImage
                                src="/project-demo/campus-eats-team.png"
                                wrapperWidth="80%"
                                speed={1.23}
                                z={0}
                                tag="Our Team"
                                initialOffset={-130}
                            />
                        </div>
                    </div>

                    {/* Row 4: Hatchery & Jan 2025 */}
                    <div className="flex flex-wrap p-0">
                        <div className="w-full md:w-1/2 m-0 p-0">
                            <p className="md w-4/5 mx-auto my-2" style={{
                                marginTop: isXS ? '8vh' : '3vh',
                                marginBottom: isXS ? '8vh' : '5vh'
                            }}>
                                In Summer 2024, we joined the UofT Hatchery Program to enhance Campus Eats by adding new features, strengthening our business model.
                            </p>
                            {isXS && (
                                <ScrollImage
                                    src="/project-demo/uoft-hatchery.png"
                                    wrapperWidth="90%"
                                    speed={1.2}
                                    z={0}
                                    tag="Hatchery"
                                    initialOffset={0}
                                    overlayText="Watch our Hatchery Pitch"
                                    overlayAction={() => openVideo('https://youtu.be/-iV_XxbnngA')}
                                />
                            )}
                            {!isXS && (
                                <ScrollImage
                                    src="/project/campus-eats.png"
                                    wrapperWidth="90%"
                                    speed={1}
                                    z={0}
                                    tag="Jan 2025"
                                    initialOffset={0}
                                    overlayText="Watch our Full Demo"
                                    overlayAction={() => openVideo('https://youtu.be/nSsBSv1q4Bc')}
                                />
                            )}
                        </div>
                        <div className="w-full md:w-1/2 m-0 p-0">
                            {!isXS && (
                                <ScrollImage
                                    src="/project-demo/uoft-hatchery.png"
                                    wrapperWidth="90%"
                                    speed={1.2}
                                    z={0}
                                    tag="Hatchery"
                                    initialOffset={0}
                                    overlayText="Watch our Hatchery Pitch"
                                    overlayAction={() => openVideo('https://youtu.be/-iV_XxbnngA')}
                                />
                            )}
                            <p className="md w-4/5 mx-auto my-2" style={{
                                marginTop: isXS ? '8vh' : '0',
                                marginBottom: isXS ? '8vh' : '10vh'
                            }}>
                                Finally, we redesigned Campus Eats with a sleek modern UI, expanded features, and a scalable backend—delivering a faster, smarter, and more seamless student dining experience.
                            </p>
                            {isXS && (
                                <ScrollImage
                                    src="/project/campus-eats.png"
                                    wrapperWidth="90%"
                                    speed={1}
                                    z={0}
                                    tag="Jan 2025"
                                    initialOffset={0}
                                    overlayText="Watch our Full Demo"
                                    overlayAction={() => openVideo('https://youtu.be/nSsBSv1q4Bc')}
                                />
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
}
