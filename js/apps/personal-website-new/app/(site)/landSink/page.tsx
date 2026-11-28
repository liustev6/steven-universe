'use client';

import { useState } from 'react';
import { ImPointLeft } from 'react-icons/im';
import FadeIn from '@/components/effects/FadeIn';

/**
 * LandSink Project Page
 *
 * Features:
 * - Linear regression model to predict land submersion due to rising sea levels
 * - Interactive year input for predictions
 * - Opens prediction results in popup window
 * - Built with Django backend hosted on AWS
 */

export default function LandSink() {
    const [year, setYear] = useState(2020);

    const request = () => {
        const url = `https://server-lite.liustev6.ca/landsink/predict/${year}`;
        const newWindow = window.open(url, 'Dialog', 'width=600,height=400');

        if (!newWindow) {
            alert('Popup blocked. Please allow popups for this site.');
        }
    };

    const handleSubmit = (event: React.FormEvent) => {
        event.preventDefault();
        request();
    };

    const handleOnChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        setYear(Number(event.target.value));
    };

    return (
        <section className="relative text-white text-center px-[10vw] py-[20vh]">
            {/* Description and Image Section */}
            <div className="flex flex-wrap">
                <div className="w-full xl:w-7/12">
                    <FadeIn>
                        <h3>
                            Estimate <span className="text-primary">LandSink</span> Percentage
                        </h3>
                    </FadeIn>
                    <FadeIn>
                        <p className="text-justify pt-5">
                            This project represents the culminating effort of CSC110, where our team gathered sea level
                            data, temperature data, and elevation data from various locations worldwide. Our objective
                            was to construct a linear regression model capable of predicting the percentage of country
                            submerged and the future global average temperature.
                        </p>
                    </FadeIn>
                    <FadeIn>
                        <p className="text-justify pt-5">
                            Originally, we built the project using Pygame, which generated HTML files upon each new
                            value request. For more details about the original version, please refer to our Github
                            Repository linked{' '}
                            <a
                                href="https://github.com/Yuanxyyds/CSC110PredictLandSink"
                                className="text-primary no-underline hover:underline cursor-pointer font-bold"
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                here
                            </a>
                            .
                        </p>
                    </FadeIn>
                    <FadeIn>
                        <p className="text-justify pt-5">
                            To enhance interactivity on the web, I recently developed a Django back-end for this
                            project. This allows users to send API requests for predictions. The back-end server is now
                            hosted on AWS. You can try out the prediction feature <strong className="text-primary">below</strong>.
                        </p>
                    </FadeIn>
                </div>
                <div className="w-full xl:w-1/12" />
                <div className="w-full xl:w-4/12 text-center pb-5">
                    <FadeIn>
                        <img
                            src="/project/land-sink.png"
                            alt="Land Sink"
                            className="max-w-100 mt-16"
                        />
                    </FadeIn>
                </div>
            </div>

            {/* Prediction Section */}
            <div className="mt-[12vh] mb-4">
                <FadeIn>
                    <h3>
                        Make a <span className="text-primary">Prediction</span>
                    </h3>
                </FadeIn>
                <FadeIn>
                    <h5 className="pt-5">
                        <strong className="text-primary">Note:</strong> Enter some large numbers as input year
                    </h5>
                </FadeIn>
                <FadeIn>
                    <div className="pt-5">
                        <form onSubmit={handleSubmit} className="flex items-center justify-center gap-2.5">
                            <input
                                type="number"
                                value={year}
                                onChange={handleOnChange}
                                placeholder="Enter the year"
                                className="border border-[#555] text-black px-2 py-2 bg-white rounded-sm text-base outline-none transition-colors"
                            />
                            <button
                                type="submit"
                                className="bg-transparent border-none text-white cursor-pointer text-lg flex items-center gap-1 transition-all duration-500 hover:scale-110"
                            >
                                <ImPointLeft /> Generate
                            </button>
                        </form>
                    </div>
                </FadeIn>
            </div>
        </section>
    );
}
