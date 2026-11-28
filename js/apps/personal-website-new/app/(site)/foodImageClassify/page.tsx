'use client';

import { useState, useEffect, useCallback } from 'react';
import FileDropUpload from '@/components/common/FileDropUpload';
import { useResizeManager } from '@/hooks/useResizeManager';
import FadeIn from '@/components/effects/FadeIn';

/**
 * Food 101 Image Classification page
 *
 * Features:
 * - Upload food images for classification
 * - Real-time predictions from 4 different models
 * - Responsive results table showing top 2 predictions
 * - Model performance visualization
 */

interface ModelPrediction {
    model: string;
    first_predicted_class: string;
    first_predicted_prob: number;
    second_predicted_class: string;
    second_predicted_prob: number;
}

export default function FoodImageClassify() {
    const [isProcessing, setIsProcessing] = useState(false);
    const [modelResult, setModelResult] = useState<ModelPrediction[] | null>(null);
    const [isSmallScreen, setIsSmallScreen] = useState(false);

    // Handle window resize for responsive table
    const handleResize = useCallback(() => {
        setIsSmallScreen(window.innerWidth < 576);
    }, []);

    useResizeManager(handleResize);

    // Initialize on mount
    useEffect(() => {
        setIsSmallScreen(window.innerWidth < 576);
    }, []);

    const handleUpload = async (file: File) => {
        if (!file) {
            console.log("No File Provided!");
            return;
        }

        if (isProcessing) {
            console.log("Request Already Sent!");
            return;
        }

        setIsProcessing(true);
        setModelResult(null);

        try {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/food101', {
                method: 'POST',
                body: formData,
            });

            const data = await response.json();

            if (data.predictions) {
                setModelResult(data.predictions);
                console.log("File uploaded and parsed successfully", data.predictions);
            } else if (data.error) {
                console.error(`Error: ${data.error}`);
            } else {
                console.log('No response received.');
            }
        } catch (error) {
            console.error("Error uploading the file:", error);
        } finally {
            setIsProcessing(false);
        }
    };

    return (
        <section className="relative text-white text-center px-[10vw] py-[20vh]">
            {/* Description and Image Section */}
            <div className="flex flex-wrap">
                <div className="w-full xl:w-7/12">
                    <FadeIn>
                        <h3>
                            Food Classification with <span className="text-primary">22</span> Class
                        </h3>
                    </FadeIn>
                    <FadeIn>
                        <p className="text-justify pt-5">
                            This project is focused on developing <span className="text-primary">a deep learning-based
                                food classification system</span> using the Food-101 dataset. It involves preparing and
                            processing image data, building and training models to classify <span
                                className="text-primary">22 food categories</span>, and fine-tuning the models
                            for improved accuracy.
                        </p>
                    </FadeIn>
                    <FadeIn>
                        <p className="text-justify pt-5">
                            The project integrates data augmentation, transfer learning, and hyperparameter tuning to
                            optimize performance. It trained or fine tuned <span className="text-primary">a Baseline
                                Model, a VGG Model, a Inception Model and a ResNet Model.</span> The goal is to create an
                            efficient, accurate system capable of classifying food images. <span
                                className="text-primary">This project is hosted within my home server</span>
                        </p>
                    </FadeIn>
                    <FadeIn>
                        <p className="text-justify pt-5">
                            <span className="text-primary">Try to classify a food photo below!</span> Support food types include: Apple Pie, Baby Back Ribs, Bibimbap, Caesar Salad, Cheesecake,
                            Chicken Curry, Chicken Wings, Club Sandwich, Donuts, Dumplings, French Fries, Hot Dog, Hamburger,
                            Frozen Yogust, Pizza, Ramen, Steak, Ice Cream, Waffles, Spring Rolls, Sushi, Fish and Chips.
                        </p>
                    </FadeIn>
                </div>
                <div className="w-full xl:w-1/12" />
                <div className="w-full xl:w-4/12">
                    <FadeIn>
                        <img
                            src="/project-demo/machine-learning.jpg"
                            alt="Machine Learning"
                            className="max-w-100 mt-16"
                        />
                    </FadeIn>
                </div>
            </div>


            {/* Model Performance Section */}
            <div className="lg:px-[8vw] mt-[12vh] mb-4">
                <FadeIn>
                    <h3 className="mb-2">
                        Model <span className="text-primary">Performance</span>
                    </h3>
                </FadeIn>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 justify-items-center pt-2.5">
                    <div className="w-full">
                        <FadeIn>
                            <img
                                src="/project-demo/accuracy.png"
                                alt="Accuracy"
                                className="w-full max-w-200 mt-2.5"
                            />
                        </FadeIn>
                    </div>
                    <div className="w-full">
                        <FadeIn>
                            <img
                                src="/project-demo/loss.png"
                                alt="Loss"
                                className="w-full max-w-200 mt-2.5"
                            />
                        </FadeIn>
                    </div>
                </div>
            </div>

            {/* Make a Prediction Section */}
            <div className="mt-[12vh] mb-4 lg:px-[4vw]">
                <FadeIn>
                    <h3 className="mb-2">
                        Make a <span className="text-primary">Prediction</span>
                    </h3>
                </FadeIn>

                <FadeIn>
                    <FileDropUpload
                        submitText="CLICK ME TO CLASSIFY"
                        onSubmit={handleUpload}
                        isProcessing={isProcessing}
                        processingMessage="Processing...Estimated 20 seconds"
                    />
                </FadeIn>

                {/* Prediction Result Section */}
                {modelResult != null && (
                    <div className="w-full mt-[12vh] mb-4">
                        <FadeIn className="mb-4">
                            <h3>
                                Prediction <span className="text-primary">Result</span> - {modelResult[3].first_predicted_class}
                            </h3>
                        </FadeIn>
                        <table className="w-full border-collapse border border-white">
                            <thead>
                                <tr>
                                    <th className="p-2.5">Model</th>
                                    {isSmallScreen ? (
                                        <>
                                            <th className="p-2.5">1st/2nd Predicted Class</th>
                                            <th className="p-2.5">1st/2nd Probability (%)</th>
                                        </>
                                    ) : (
                                        <>
                                            <th className="p-2.5">1st Predicted Class</th>
                                            <th className="p-2.5">Probability (%)</th>
                                            <th className="p-2.5">2nd Predicted Class</th>
                                            <th className="p-2.5">Probability (%)</th>
                                        </>
                                    )}
                                </tr>
                            </thead>
                            <tbody>
                                {modelResult.map((prediction, index) => (
                                    <tr key={index}>
                                        <td className="p-2.5">{prediction.model}</td>
                                        {isSmallScreen ? (
                                            <>
                                                <td className="p-2.5">
                                                    {`${prediction.first_predicted_class}/${prediction.second_predicted_class}`}
                                                </td>
                                                <td className="p-2.5">
                                                    {`${(prediction.first_predicted_prob * 100).toFixed(2)}/${(prediction.second_predicted_prob * 100).toFixed(2)}`}
                                                </td>
                                            </>
                                        ) : (
                                            <>
                                                <td className="p-2.5">{prediction.first_predicted_class}</td>
                                                <td className="p-2.5">{(prediction.first_predicted_prob * 100).toFixed(2)}</td>
                                                <td className="p-2.5">{prediction.second_predicted_class}</td>
                                                <td className="p-2.5">{(prediction.second_predicted_prob * 100).toFixed(2)}</td>
                                            </>
                                        )}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </section>
    );
}
