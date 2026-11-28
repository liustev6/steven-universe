'use client';

import { useState, useEffect, useCallback } from 'react';
import FadeIn from "@/components/effects/FadeIn";
import AnimatedUnderline from "@/components/effects/AnimatedUnderline";
import FeaturedProjectCard from "@/components/project/FeaturedProjectCard";
import { StackCard } from "@/components/skills/StackCard";
import { useRouter } from "next/navigation";
import { useResizeManager } from "@/hooks/useResizeManager";
import clsx from "clsx";
import {
    DiPython, DiJava, DiDart, DiSwift, DiReact,
} from "react-icons/di";
import { TbBrandKotlin, TbCircleLetterCFilled, TbDeviceVisionProFilled, TbSql } from "react-icons/tb";
import {
    SiNextdotjs, SiFirebase, SiGooglecloud, SiTailwindcss,
    SiPytorch, SiProxmox,
    SiFastapi,
    SiKubernetes,
} from "react-icons/si";
import { FaAws, FaCloudflare, FaDocker, FaFigma, FaGithub, FaLinux, FaRProject, FaYCombinator } from "react-icons/fa";
import { AiFillAppstore } from "react-icons/ai";
import { RiJavascriptFill } from "react-icons/ri";
import { BiLogoCPlusPlus, BiLogoTypescript, BiLogoFlutter, BiLogoNodejs } from "react-icons/bi";
import { FaDebian } from "react-icons/fa6";
import { GrSystem } from "react-icons/gr";
import { GiArtificialIntelligence } from "react-icons/gi";

export default function Project() {
    const router = useRouter();
    const [isVertical, setIsVertical] = useState(false);

    const checkLayout = useCallback(() => {
        const ratio = window.innerHeight / window.innerWidth;
        setIsVertical(ratio > 1.3);
    }, []);

    useResizeManager(checkLayout);

    // Check layout on mount
    useEffect(() => {
        checkLayout();
    }, [checkLayout]);

    return (
        <section>
            {/* Hero Section */}
            {isVertical ? (
                // Vertical (Mobile) Layout
                <div className="h-screen m-0 p-0 text-white">
                    <div className="pt-[20vh] px-[5vw]">
                        <h1 className={clsx(
                            "font-light mb-0 flex items-center",
                            "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:0ms]",
                            "after:content-[''] after:w-20 after:h-1 after:bg-primary after:ml-5"
                        )}>
                            EXPLORE MY
                        </h1>
                        <h1 className={clsx(
                            "mb-2 text-primary",
                            "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:300ms]"
                        )}>
                            WORKS & SKILLS
                        </h1>
                        <h5 className={clsx(
                            "mb-6 font-bold",
                            "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:500ms]"
                        )}>
                            A curated collection of what I've designed, built, and deployed — from backend systems to interactive UIs.
                        </h5>
                    </div>

                    <div className="p-0 m-0 bg-black/60 opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:700ms]">
                        <video
                            src="/project/project.mp4"
                            className="w-full"
                            autoPlay
                            muted
                            loop
                            playsInline
                        />
                    </div>

                    <div className="pt-4 px-[5vw]">
                        <h4 className="underline cursor-pointer opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:700ms]">
                            Let's Go
                        </h4>
                    </div>
                </div>
            ) : (
                // Horizontal (Desktop) Layout
                <div className="relative h-screen w-full m-0 p-0 overflow-hidden bg-black/60">
                <video
                    className="absolute top-0 left-0 w-full h-full object-cover -z-10 pointer-events-none"
                    src="/project/project.mp4"
                    autoPlay
                    muted
                    loop
                    playsInline
                />

                <div className="w-full h-full px-[8vw] pb-[15vh] flex items-center">
                    <div className="w-full md:w-11/15 lg:w-3/5">
                        <FadeIn>
                            <h1 className={clsx(
                                "font-light mb-0 flex items-center",
                                "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:0ms]",
                                "after:content-[''] after:w-20 after:h-1 after:bg-primary after:ml-5"
                            )}>EXPLORE MY</h1>
                        </FadeIn>
                        <FadeIn>
                            <h2 className={clsx("mb-2 text-primary font-bold",
                                "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:300ms]")}>WORKS & SKILLS</h2>
                        </FadeIn>
                        <FadeIn>
                            <p className={clsx(
                                "ml-1 mt-2 font-semibold",
                                "opacity-0 animate-[fadeIn_0.6s_ease-in-out_forwards] [animation-delay:500ms]")}>
                                A curated collection of what I've designed, built, and deployed — from backend systems to interactive UIs.
                            </p>
                        </FadeIn>
                    </div>
                </div>
                </div>
            )}

            {/* Projects Section */}
            <div className={clsx("px-[8vw] text-white", isVertical ? "pt-0 pb-[15vh]" : "py-[15vh]")}>
                <div className="m-0 p-0">
                    <FadeIn>
                        <AnimatedUnderline className="mb-4 inline-block">
                            <h2 className="mb-0">MY WORKS</h2>
                        </AnimatedUnderline>
                    </FadeIn>

                    <div className="flex flex-wrap">
                        {/* Row 1 - Two large cards */}
                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 lg:pr-4 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/campus-eats-cover.jpg"
                                    logoSrc="/project/campus-eats-logo-transparent.png"
                                    title="Campus Eats"
                                    description="To tackle challenges in campus dining, our startup team developed an all-encompassing mobile app for campus food service. This app serves students, restaurants, and drivers, aiming to improve the campus food service ecosystem. It was built with Flutter and a Firebase backend."
                                    shortDescription="Our startup team built a mobile app to streamline campus dining for students, restaurants, and drivers using Flutter and Firebase."
                                    githubLink="https://github.com/CampusEatsUofT"
                                    tags={['Mobile App', 'Website', 'Business/Startup', 'UI/UX Design']}
                                    onDetailClick={() => {
                                        router.push('/campusEats');
                                    }}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 lg:pl-4 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/steven-ai-cover.jpg"
                                    logoSrc="/project/stevenai.webp"
                                    title="Steven AI"
                                    description="Steven AI is a personalized chatbot designed to answer any questions about me. It was built by fine-tuning the LLaMA 3.2 model (3 billion parameters) on over 1,000 Q&A pairs covering my background and experiences. To optimize training in my home lab, I used a LoRA adapter for parameter-efficient fine-tuning and RAG to enhance accuracy and response relevance."
                                    shortDescription="Steven AI is a personalized chatbot fine-tuned on 1,000+ Q&A pairs about me using LLaMA 3.2 and LoRA. It runs in my home lab and uses RAG to improve accuracy and relevance."
                                    githubLink="https://github.com/Yuanxyyds/machine-learning/tree/main/llama3.2"
                                    tags={['Machine Learning', 'LLM Finetuning', 'RAG']}
                                    onDetailClick={() => {
                                        router.push('/stevenAi');
                                    }}
                                />
                            </FadeIn>
                        </div>

                        {/* Row 2 - Smaller cards */}
                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pr-4 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/portfolio-cover.jpg"
                                    title="Portfolio Website"
                                    description="My portfolio website is built with a React frontend and a Django backend, self-hosted on a Proxmox server. It showcases my projects, design work, and passion for building visually engaging, innovative products."
                                    shortDescription="My portfolio website, built with React and Django and self-hosted on Proxmox, showcases my projects, designs, and passion for creating visually engaging products."
                                    githubLink="https://github.com/Yuanxyyds/MyPersonalWebsite2.0"
                                    tags={['React', 'Django', 'UI/UX Design']}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pl-4 xl:pl-2 xl:pr-2 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/server-room-cover.jpg"
                                    title="3D Server Room"
                                    description="A 3D-rendered server room built with Blender and Three.js, providing an interactive visualization of my setup. Explore my background and hardware by interacting with monitors, a TV, and other elements."
                                    shortDescription="A 3D server room built with Blender and Three.js, showcasing my setup and hardware through interactive elements."
                                    tags={['3D Modeling', 'Blender', 'Three.js']}
                                    detailText="Visit"
                                    onDetailClick={() => {
                                        router.push('/server');
                                    }}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pr-4 xl:pl-4 xl:pr-0 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/food-101-cover.jpg"
                                    title="Food-101 Classification"
                                    description="This project develops a deep learning model to classify food images using the Food-101 dataset. It covers data processing, model training, transfer learning, and hyperparameter tuning, comparing VGG, Inception, and ResNet against a baseline to build an efficient, accurate classifier."
                                    shortDescription="Built a food image classifier using Food-101, comparing VGG, Inception, and ResNet with a baseline through transfer learning and tuning for optimal accuracy."
                                    githubLink="https://github.com/Yuanxyyds/machine-learning/tree/main/food-101"
                                    tags={['Deep Learning', 'CNN']}
                                    onDetailClick={() => {
                                        router.push('/foodImageClassify');
                                    }}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pl-4 xl:pl-0 xl:pr-4 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/lockin-cover.jpg"
                                    title="LockIn IOS"
                                    description="LockIn is an iOS app that helps users reduce screen time by blocking distracting apps, starting focus sessions, and earning coins for staying offline. Users can redeem real-life rewards like food and clothing. Built with Flutter, Swift, and Firebase."
                                    shortDescription="LockIn helps users block apps, stay focused, and earn real-life rewards. Built with Flutter and Firebase, it promotes digital wellness through focus sessions, screen time limits."
                                    githubLink="https://justlockin.com/"
                                    detailText="App Store"
                                    tags={['Mobile App', 'Flutter', 'Website']}
                                    onDetailClick={() => {
                                        window.open("https://apps.apple.com/ca/app/lockbox-screentime-tool/id6740202232");
                                    }}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pr-4 xl:pl-2 xl:pr-2 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/mentor-ai-cover.jpg"
                                    title="Mentor AI"
                                    description="MentorAI is a research project that uses YouTube links and user data to generate personalized mentorship advice. It guides researchers using LLMs and NLP on transcripts from mentorship-related videos."
                                    shortDescription="MentorAI generates personalized mentorship advice from YouTube videos and user data, helping aspiring researchers find mentors, review literature, and prepare for PhD applications."
                                    detailText="TEP Unavailable"
                                    tags={['Machine Learning', 'RAG', 'Research']}
                                    onDetailClick={() => {
                                        router.push('/mentorAi');
                                    }}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pl-4 xl:pl-4 xl:pr-0 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/dtc-cover.jpg"
                                    title="JCI DTC Community"
                                    description="Community is a mobile app by Johnson Controls for home security and cloud device management. I contributed to both front-end and back-end development using Flutter and Firebase, supporting real-time features and secure device interactions."
                                    shortDescription="Community is a mobile app by Johnson Controls for home security and cloud device management. Worked on front-end and back-end development using Flutter and Firebase."
                                    tags={['Flutter', 'Home Assistant', 'GCP']}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pr-4 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/fdl-cover.jpg"
                                    title="Accelerated DBMS"
                                    description="As Dennard scaling ends and Moore's law slows, enhancing general-purpose processor performance becomes harder. This research explores optimizing data management systems with cloud hardware accelerators to boost data task efficiency and reduce costs at scale."
                                    shortDescription="This research explores optimizing data management systems with cloud hardware accelerators to boost data task efficiency and reduce costs at scale."
                                    tags={['GPU/FPGA', 'Research']}
                                    detailText="Website"
                                    onDetailClick={() => {
                                        window.open("https://fardatalab.org/research.html");
                                    }}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pl-4 xl:pl-2 xl:pr-2 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/great-lake-cover.jpg"
                                    title="Great Lakes Pollution"
                                    description="This project explores Great Lakes water quality with a focus on phosphorus levels. It uses data from the Canadian Open Data Portal and city web scraping, featuring visualizations like time series plots, boxplots, and interactive maps."
                                    shortDescription="Analyzed Great Lakes water quality using public and scraped data, focusing on phosphorus levels. Visualized trends through time series plots, boxplots, and interactive maps."
                                    githubLink="https://github.com/Yuanxyyds/great-lakes-pollution-research"
                                    detailText="Visit"
                                    tags={['Data Science', 'Data Wrangling']}
                                    onDetailClick={() => {
                                        window.open("https://yuanxyyds.github.io/great-lakes-pollution-research/");
                                    }}
                                />
                            </FadeIn>
                        </div>

                        <div className="w-full xs:w-10/12 sm:w-9/12 md:w-9/12 lg:w-1/2 xl:w-1/3 lg:pr-4 xl:pl-4 xl:pr-0 pt-6">
                            <FadeIn>
                                <FeaturedProjectCard
                                    imageSrc="/project/land-sink-cover.jpg"
                                    title="LandSink Estimate"
                                    description="For our CSC110 Final Project, we trained a Python model to estimate temperature and land sink percentages by year. I extended the project by developing a Django backend hosted on AWS, enabling interactive web access and user engagement."
                                    shortDescription="Built a Python model to estimate climate data, then added a Django backend hosted on AWS to enable web-based user interaction for the CSC110 final project."
                                    tags={['Regression', 'Django', 'AWS']}
                                    onDetailClick={() => {
                                        router.push('/landSink');
                                    }}
                                />
                            </FadeIn>
                        </div>
                    </div>
                </div>
            </div>

            {/* Skills Section */}
            <div className="px-[8vw] text-white">
                <FadeIn>
                    <AnimatedUnderline className="mb-0 inline-block">
                        <h2 className="mb-0">SKILLS</h2>
                    </AnimatedUnderline>
                </FadeIn>

                <div className="p-0 pt-4">
                    <FadeIn>
                        <h3 className="mb-4">
                            Programming Language <strong className="text-primary">Skill</strong>
                        </h3>
                    </FadeIn>
                    <FadeIn>
                        <StackCard
                            skills={[
                                { icon: <DiPython className="text-[5em]" />, name: "Python", stars: 6 },
                                { icon: <DiJava className="text-[5em]" />, name: "Java", stars: 4 },
                                { icon: <BiLogoTypescript className="text-[5em]" />, name: "TypeScript", stars: 6 },
                                { icon: <RiJavascriptFill className="text-[5em]" />, name: "Javascript", stars: 6 },
                                { icon: <DiDart className="text-[5em]" />, name: "Dart", stars: 6 },
                                { icon: <TbSql className="text-[5em]" />, name: "SQL", stars: 5 },
                                { icon: <TbCircleLetterCFilled className="text-[5em]" />, name: "C Language", stars: 4 },
                                { icon: <BiLogoCPlusPlus className="text-[5em]" />, name: "C++", stars: 4 },
                                { icon: <TbBrandKotlin className="text-[5em]" />, name: "Kotlin", stars: 3 },
                                { icon: <DiSwift className="text-[5em]" />, name: "Swift", stars: 3 },
                                { icon: <FaRProject className="text-[5em]" />, name: "R Language", stars: 4 },
                                { icon: <SiTailwindcss className="text-[5em]" />, name: "CSS", stars: 5 },
                            ]}
                        />
                    </FadeIn>
                </div>

                <div className="p-0 pt-4">
                    <FadeIn>
                        <h3 className="mb-4">
                            Framework & Service <strong className="text-primary">Skill</strong>
                        </h3>
                    </FadeIn>
                    <FadeIn>
                        <StackCard
                            skills={[
                                { icon: <BiLogoFlutter className="text-[5em]" />, name: "Flutter", stars: 6 },
                                { icon: <DiReact className="text-[5em]" />, name: "React", stars: 6 },
                                { icon: <SiNextdotjs className="text-[5em]" />, name: "NextJs", stars: 6 },
                                { icon: <BiLogoNodejs className="text-[5em]" />, name: "NodeJs", stars: 6 },
                                { icon: <SiFastapi className="text-[5em]" />, name: "FastAPI", stars: 5 },
                                { icon: <SiPytorch className="text-[5em]" />, name: "PyTorch", stars: 5 },
                                { icon: <FaLinux className="text-[5em]" />, name: "Linux", stars: 4 },
                                { icon: <FaDebian className="text-[5em]" />, name: "Debian", stars: 4 },
                                { icon: <SiProxmox className="text-[5em]" />, name: "Proxmox", stars: 5 },
                                { icon: <SiFirebase className="text-[5em]" />, name: "Firebase", stars: 6 },
                                { icon: <SiGooglecloud className="text-[5em]" />, name: "Google Cloud Platform", stars: 5 },
                                { icon: <FaAws className="text-[5em]" />, name: "AWS Infra", stars: 5 },
                                { icon: <SiKubernetes className="text-[5em]" />, name: "Kubernetes", stars: 4 },
                                { icon: <FaFigma className="text-[5em]" />, name: "Figma", stars: 6 },
                                { icon: <FaDocker className="text-[5em]" />, name: "Docker", stars: 5 },
                                { icon: <FaCloudflare className="text-[5em]" />, name: "Cloudflare", stars: 4 },
                                { icon: <FaGithub className="text-[5em]" />, name: "Git CI/CD", stars: 6 },
                            ]}
                        />
                    </FadeIn>
                </div>

                <div className="p-0 pt-4">
                    <FadeIn>
                        <h3 className="mb-4">
                            Areas of <strong className="text-primary">Expertise</strong>
                        </h3>
                    </FadeIn>
                    <FadeIn>
                        <StackCard
                            skills={[
                                { icon: <GiArtificialIntelligence className="text-[5em]" />, name: "AI Software Agents", stars: 6 },
                                { icon: <AiFillAppstore className="text-[5em]" />, name: "Full-Stack Engineer", stars: 6 },
                                { icon: <div className="text-[3em]">ML</div>, name: "Machine Learning", stars: 6 },
                                { icon: <TbDeviceVisionProFilled className="text-[5em]" />, name: "Computer Vision", stars: 4 },
                                { icon: <GrSystem className="text-[5em]" />, name: "Operating System", stars: 5 },
                                { icon: <FaYCombinator className="text-[5em]" />, name: "Startups", stars: 6 },
                            ]}
                        />
                    </FadeIn>
                </div>
            </div>
        </section >
    );
}
