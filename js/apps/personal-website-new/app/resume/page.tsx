'use client';

import { useEffect, useState, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import { AiOutlineDownload } from "react-icons/ai";
import { useResizeManager } from "@/hooks/useResizeManager";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

/**
 * Resume page component
 *
 * Features:
 * - PDF viewer with react-pdf (dynamically loaded for SSR compatibility)
 * - Responsive scaling based on viewport width
 * - Download button for resume PDF
 * - Clean layout with centered content
 */

// ✅ Dynamically load react-pdf ONLY on client
const PDFViewer = dynamic(
    async () => {
        const mod = await import("react-pdf");

        // Worker version must match the PDF.js bundled with react-pdf.
        mod.pdfjs.GlobalWorkerOptions.workerSrc =
            `https://unpkg.com/pdfjs-dist@${mod.pdfjs.version}/build/pdf.worker.min.mjs`;

        // react-pdf 11 suspends by default; this page has no Suspense/Error
        // boundary, so keep the built-in loading and error states.
        return function PDF({ scale }: { scale: number }) {
            return (
                <mod.Document
                    file="/resume/resume.pdf"
                    suspense={false}
                    loading="Loading resume…"
                    error="Could not load the resume. Use the download link above."
                >
                    <mod.Page pageNumber={1} scale={scale} />
                </mod.Document>
            );
        };
    },
    { ssr: false }
);

export default function Resume() {
    const [scale, setScale] = useState(1.7);
    const lastScaleRef = useRef(1.7);

    const calculateScale = (width: number) => {
        return Math.min(Math.max(width / 1200 * 1.7, 0.6), 1.7);
    };

    const handleResize = useCallback(() => {
        const newScale = calculateScale(window.innerWidth);
        const scaleDiff = Math.abs(newScale - lastScaleRef.current);

        // Only update if scale difference > 0.1
        if (scaleDiff > 0.1) {
            lastScaleRef.current = newScale;
            setScale(newScale);
        }
    }, []);

    useResizeManager(handleResize);

    useEffect(() => {
        handleResize();
    }, [handleResize]);

    return (
        <section className="pt-27.5 pb-7.5 min-h-screen text-center text-white">
            <a
                href="/resume/resume.pdf"
                target="_blank"
                className="inline-flex items-center gap-2 px-6 py-3 rounded-md font-semibold mb-6"
            >
                <AiOutlineDownload />
                Updated at Jan 2026
            </a>

            <div className="flex justify-center pb-12.5">
                <PDFViewer scale={scale} />
            </div>
        </section>
    );
}