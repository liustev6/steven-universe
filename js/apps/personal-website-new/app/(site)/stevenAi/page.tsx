'use client';

import { useState } from "react";
import clsx from "clsx";
import { sendChatMessage } from "./controller";

/**
 * Steven AI chatbot page
 *
 * Features:
 * - Personalized AI chatbot with multiple base models (GPT-4o, fine-tuned LLaMA)
 * - RAG (Retrieval-Augmented Generation) with QA pairs and fact docs
 * - Real-time chat interface with message history
 * - Configurable model and retrieval strategy
 * - Hero section with project description
 * - Animated neon text effects
 */
export default function StevenAI() {
    const [messages, setMessages] = useState<Array<{ sender: string; text: string }>>([]);
    const [messageInput, setMessageInput] = useState("");
    const [showPopup, setShowPopup] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [selectedModel, setSelectedModel] = useState("chatGPT-5");
    const [selectedRAG, setSelectedRAG] = useState(["QA Pairs", "Docs of Facts"]);

    // Neon text effect classes (matches CodeCard implementation)
    const neonTextClasses = "font-bold bg-[linear-gradient(90deg,#ff00cc,#00ccff,#8000ff,#b300ff,#ff00cc)] bg-size-[400%_100%] bg-clip-text text-transparent animate-[neonColorFlow_6s_ease-in-out_infinite]";

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!messageInput.trim() || isLoading) return;

        const userMessage = messageInput.trim();
        setMessageInput("");
        setMessages((prev) => [...prev, { sender: "You", text: userMessage }]);
        setIsLoading(true);

        // Determine last Q and A from message history
        let lastQuestion: string | undefined = undefined;
        let lastAnswer: string | undefined = undefined;
        for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].sender === "You" && !lastQuestion) lastQuestion = messages[i].text;
            if (messages[i].sender === "AI" && !lastAnswer) lastAnswer = messages[i].text;
            if (lastQuestion && lastAnswer) break;
        }

        try {
            await sendChatMessage(
                userMessage,
                {
                    model: selectedModel,
                    ragOptions: selectedRAG,
                    lastQuestion,
                    lastAnswer
                },
                {
                    onDelta: (_delta, accumulatedText) => {
                        setMessages((prev) => {
                            const lastMessage = prev[prev.length - 1];

                            // If last message is from AI, update it
                            if (lastMessage && lastMessage.sender === "AI") {
                                const updated = [...prev];
                                updated[updated.length - 1] = {
                                    sender: "AI",
                                    text: accumulatedText
                                };
                                return updated;
                            }

                            // Otherwise add new AI message
                            return [...prev, { sender: "AI", text: accumulatedText }];
                        });
                    },
                    onDone: (fullText) => {
                        setMessages((prev) => {
                            const lastMessage = prev[prev.length - 1];

                            // If last message is from AI, update it
                            if (lastMessage && lastMessage.sender === "AI") {
                                const updated = [...prev];
                                updated[updated.length - 1] = {
                                    sender: "AI",
                                    text: fullText
                                };
                                return updated;
                            }

                            // Otherwise add new AI message (no deltas received)
                            return [...prev, { sender: "AI", text: fullText }];
                        });
                    },
                    onError: (error) => {
                        setMessages((prev) => {
                            const lastMessage = prev[prev.length - 1];

                            // If last message is from AI, update it
                            if (lastMessage && lastMessage.sender === "AI") {
                                const updated = [...prev];
                                updated[updated.length - 1] = {
                                    sender: "AI",
                                    text: `Error: ${error}`
                                };
                                return updated;
                            }

                            // Otherwise add new error message
                            return [...prev, { sender: "AI", text: `Error: ${error}` }];
                        });
                    }
                }
            );
        } catch (error) {
            console.error('Error:', error);
            setMessages((prev) => [...prev, { sender: "AI", text: `Error: ${(error as Error).message}` }]);
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <section>
            {/* Config Popup */}
            {showPopup && (
                <div
                    className="fixed inset-0 bg-black/40 flex items-center justify-center z-9999"
                    onClick={() => setShowPopup(false)}
                >
                    <div
                        className="bg-[#1f1f1f] p-5 rounded-xl text-white min-w-80 shadow-2xl"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <label className="block font-semibold text-sm">
                            Base Model:
                            <select
                                value={selectedModel}
                                onChange={(e) => setSelectedModel(e.target.value)}
                                className="w-full p-1.5 mt-1.5 rounded-md border-none text-sm bg-[#2a2a2a] text-white"
                            >
                                <option value="chatGPT-5">chatGPT-5</option>
                                <option value="Qwen">Qwen3-4B (Self Host)</option>
                                <option value="LLaMA">FT LLaMA-3.2-3B (Coming Soon)</option>
                            </select>
                        </label>

                        <label className="block mt-3 font-semibold text-sm">RAG Info:</label>
                        <div className="flex flex-col gap-2 mt-2">
                            {["QA Pairs", "Docs of Facts"].map((option) => (
                                <label key={option} className="flex items-center gap-2 text-sm">
                                    <input
                                        type="checkbox"
                                        checked={selectedRAG.includes(option)}
                                        onChange={() =>
                                            setSelectedRAG((prev) =>
                                                prev.includes(option)
                                                    ? prev.filter((item) => item !== option)
                                                    : [...prev, option]
                                            )
                                        }
                                    />
                                    {option}
                                </label>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* Hero Section */}
            <div className="relative h-screen min-h-195 m-0 p-0 text-white bg-black/40">
                <img
                    className="absolute top-0 left-0 w-full h-full pointer-events-none -z-10"
                    style={{ objectFit: 'fill' }}
                    src="/project-demo/stevenai.jpeg"
                    alt="steven-ai-bg"
                />
                <div className="w-full h-full px-[8vw] pt-[max(100px,15vh)] pb-[max(100px,15vh)] flex">
                    <div className="w-full sm:w-10/12 md:w-8/12 lg:w-7/12 xl:w-1/2 h-[70vh] min-h-145">
                        <div className="w-full h-full p-[8%_10%] bg-[rgba(0,0,30,0.3)] text-center">
                            <h2 className="mb-2 font-black">STEVEN AI</h2>
                            <p className="small text-left mb-4">
                                StevenAI is <span className={neonTextClasses}>a personalized AI chatbot</span> built to answer any questions me. It was originally developed by <span className={neonTextClasses}>fine-tuning LLaMA 3.2 with LoRA</span> on a custom dataset of 1,000+ Q&A pairs. However, while this method provided deep personalization, it also surfaced limitations in accuracy, especially when handling rare or nuanced queries.
                            </p>
                            <p className="small text-left mb-4">
                                To address these challenges, StevenAI was upgraded with a <span className={neonTextClasses}>retrieval-augmented generation (RAG)</span> layer, which supplements the model's responses with relevant facts from a structured knowledge base. This <span className={neonTextClasses}>hybrid architecture</span> blends the strengths of fine-tuning with real-time semantic retrieval, resulting in more accurate, flexible, and context-aware answers.
                            </p>
                            <p className="small text-left mb-4">
                                The system supports multiple <span className={neonTextClasses}>base models</span> and <span className={neonTextClasses}>retrieval strategies</span>. You can select your preferred combination and <span className={neonTextClasses}>ASK A QUESTION NOW</span>!
                            </p>
                        </div>
                    </div>
                    <div className="flex-1"></div>
                </div>
            </div>

            {/* Chat Section */}
            <div className="relative h-screen min-h-162.5 m-0 p-0 text-white bg-black/40">
                <div className="flex flex-col px-[8vw] w-full h-full">
                    <div className="h-18.75"></div>

                    <h2 className="text-center mt-2 mb-8">
                        Start a <span className={neonTextClasses}>Chat</span> now
                    </h2>

                    {/* Chat Log */}
                    <div className="flex-1 p-5 overflow-y-auto bg-[#1e1e1e] text-white rounded-xl shadow-lg border border-[#333]">
                        {messages.map((msg, index) => (
                            <div
                                key={index}
                                className={clsx(
                                    "my-2.5 px-3.5 py-2.5 rounded-xl max-w-[80%] leading-relaxed whitespace-pre-wrap wrap-break-word",
                                    msg.sender === 'You'
                                        ? "text-[#76c7c0] bg-[#3a3a3a] self-end text-left text-[0.95rem]"
                                        : "text-white bg-[#444] self-start text-left text-[0.95rem]"
                                )}
                            >
                                {msg.text}
                            </div>
                        ))}
                    </div>

                    {/* Chat Form */}
                    <form onSubmit={handleSubmit} className="flex mt-5">
                        <input
                            type="text"
                            value={messageInput}
                            onChange={(e) => setMessageInput(e.target.value)}
                            placeholder="Type a message..."
                            required
                            className="grow p-2.5 min-w-0 bg-[#333] border border-[#555] rounded-md text-white text-base"
                        />
                        <button
                            type="button"
                            onClick={() => setShowPopup((prev) => !prev)}
                            className="bg-[#76c7c0] hover:bg-[#5aa49c] border-none px-5 py-2.5 ml-2.5 text-white text-base rounded-md cursor-pointer transition-colors"
                        >
                            Config
                        </button>
                        <button
                            type="submit"
                            disabled={isLoading}
                            className="bg-[#76c7c0] hover:bg-[#5aa49c] border-none px-5 py-2.5 ml-2.5 text-white text-base rounded-md cursor-pointer transition-colors disabled:opacity-50"
                        >
                            Send
                        </button>
                    </form>

                    <div className="h-18.75"></div>
                </div>
            </div>
        </section>
    );
}
