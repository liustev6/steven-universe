import {
    createChatRequest,
    ChatResponseChunk,
    isDeltaChunk,
    isDoneChunk,
    isErrorChunk
} from "@steven-universe/shared-schemas";

/**
 * Steven AI Controller
 * Handles all business logic for chat interactions
 */

export interface ChatStreamCallbacks {
    onDelta: (delta: string, accumulatedText: string) => void;
    onDone: (fullText: string) => void;
    onError: (error: string) => void;
}

export interface ChatOptions {
    model: string;
    ragOptions: string[];
    lastQuestion?: string;
    lastAnswer?: string;
}

/**
 * Send a chat message with streaming response
 */
export async function sendChatMessage(
    query: string,
    options: ChatOptions,
    callbacks: ChatStreamCallbacks
): Promise<void> {
    const { model, ragOptions, lastQuestion, lastAnswer } = options;

    // Determine RAG settings
    const hasQA = ragOptions.includes("QA Pairs");
    const hasDocs = ragOptions.includes("Docs of Facts");

    // Map frontend model names to API model names
    let apiModel: string;
    switch (model) {
        case "ChatGPT-5":
            apiModel = "chatGPT";
            break;
        case "Qwen":
            apiModel = "Qwen3-4B-Instruct";
            break;
        case "LLaMA":
            apiModel = "llama-ft"; // Future fine-tuned LLaMA model
            break;
        default:
            apiModel = "chatGPT";
    }

    // Create request using shared schema
    const request = createChatRequest({
        query,
        last_q: lastQuestion || null,
        last_a: lastAnswer || null,
        model: apiModel,
        use_qa_pairs: hasQA,
        use_docs_of_fact: hasDocs,
        temperature: 0.7,
        max_tokens: 2048
    });

    // Call Next.js API route (always returns stream)
    const response = await fetch('/api/stevenai', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
    });

    if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
    }

    // Handle streaming response
    await handleStreamingResponse(response, callbacks);
}

/**
 * Handle streaming response and parse chunks
 */
async function handleStreamingResponse(
    response: Response,
    callbacks: ChatStreamCallbacks
): Promise<void> {
    const reader = response.body?.getReader();
    if (!reader) {
        throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let accumulatedText = '';

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || ''; // Keep incomplete line in buffer

            for (const line of lines) {
                if (line.trim()) {
                    try {
                        const chunk: ChatResponseChunk = JSON.parse(line);

                        if (isDeltaChunk(chunk)) {
                            // Append delta to accumulated text
                            accumulatedText += chunk.delta;
                            callbacks.onDelta(chunk.delta, accumulatedText);
                        } else if (isDoneChunk(chunk)) {
                            callbacks.onDone(chunk.full_text || accumulatedText);
                        } else if (isErrorChunk(chunk)) {
                            callbacks.onError(chunk.error || 'Unknown error');
                        }
                    } catch (parseError) {
                        console.error('Failed to parse chunk:', parseError, line);
                    }
                }
            }
        }
    } catch (error) {
        console.error('Stream error:', error);
        throw error;
    }
}

/**
 * Send a non-streaming chat message (get complete response at once)
 */
export async function sendChatMessageSync(
    query: string,
    options: ChatOptions
): Promise<string> {
    const { model, ragOptions, lastQuestion, lastAnswer } = options;

    // Determine RAG settings
    const hasQA = ragOptions.includes("QA Pairs");
    const hasDocs = ragOptions.includes("Docs of Facts");

    // Map frontend model names to API model names
    let apiModel: string;
    switch (model) {
        case "ChatGPT-4o":
            apiModel = "chatGPT";
            break;
        case "Qwen":
            apiModel = "Qwen3-4B-Instruct";
            break;
        case "LLaMA":
            apiModel = "llama-ft"; // Future fine-tuned LLaMA model
            break;
        default:
            apiModel = "chatGPT";
    }

    // Create request using shared schema
    const request = createChatRequest({
        query,
        last_q: lastQuestion || null,
        last_a: lastAnswer || null,
        model: apiModel,
        use_qa_pairs: hasQA,
        use_docs_of_fact: hasDocs,
        temperature: 0.7,
        max_tokens: 2048
    });

    // Call Next.js API route (always returns stream)
    const response = await fetch('/api/stevenai', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
    });

    if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
    }

    // Consume stream but only return final text
    const reader = response.body?.getReader();
    if (!reader) {
        throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let fullText = '';

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.trim()) {
                    try {
                        const chunk: ChatResponseChunk = JSON.parse(line);

                        // Only extract the final text from DONE chunk
                        if (isDoneChunk(chunk)) {
                            fullText = chunk.full_text;
                        }
                    } catch (parseError) {
                        console.error('Failed to parse chunk:', parseError);
                    }
                }
            }
        }

        if (!fullText) {
            throw new Error('No response text received');
        }

        console.log('Response:', fullText);
        return fullText;
    } catch (error) {
        console.error('Stream error:', error);
        throw error;
    }
}
