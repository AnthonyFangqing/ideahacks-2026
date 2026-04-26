import "./App.css";
import { useEffect, useMemo, useState } from "react";

type Book = {
	title?: string;
	authors_display?: string;
	identifiers?: Record<string, string> | null;
	series?: string | null;
	series_index?: string | null;
	tags?: string[];
	languages?: string[];
	pubdate?: string | null;
	publisher?: string | null;
};

type ConnectedEReader = {
	name: string;
	books: Book[];
};

type StreamMessage = {
	connected_e_reader: ConnectedEReader | null;
};

type ConnectionState = "connecting" | "connected" | "disconnected" | "error";

const getStreamUrl = () => {
	const configuredBackendUrl = import.meta.env.VITE_BACKEND_URL as
		| string
		| undefined;
	if (configuredBackendUrl) {
		const backendUrl = new URL(configuredBackendUrl);
		backendUrl.protocol = backendUrl.protocol === "https:" ? "wss:" : "ws:";
		backendUrl.pathname = "/stream";
		return backendUrl.toString();
	}

	const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
	const host =
		window.location.port === "5005" ? window.location.host : "localhost:5005";

	return `${protocol}//${host}/stream`;
};

const getBookKey = (book: Book) =>
	book.identifiers
		? JSON.stringify(book.identifiers)
		: [book.title, book.authors_display, book.publisher, book.pubdate]
				.filter(Boolean)
				.join("|");

const formatDate = (value?: string | null) => {
	if (!value) {
		return "Unknown date";
	}

	const parsed = new Date(value);
	if (Number.isNaN(parsed.getTime())) {
		return value;
	}

	return parsed.getFullYear().toString();
};

function App() {
	const [connectionState, setConnectionState] =
		useState<ConnectionState>("connecting");
	const [reader, setReader] = useState<ConnectedEReader | null>(null);
	const streamUrl = useMemo(getStreamUrl, []);

	useEffect(() => {
		const socket = new WebSocket(streamUrl);

		socket.addEventListener("open", () => {
			setConnectionState("connected");
		});

		socket.addEventListener("message", (event) => {
			const message = JSON.parse(event.data) as StreamMessage;
			setReader(message.connected_e_reader);
		});

		socket.addEventListener("close", () => {
			setConnectionState("disconnected");
		});

		socket.addEventListener("error", () => {
			setConnectionState("error");
		});

		return () => {
			socket.close();
		};
	}, [streamUrl]);

	const books = reader?.books ?? [];
	const featuredBooks = books.slice(0, 8);

	return (
		<main className="kiosk-shell">
			<section className="status-panel">
				<div>
					<p className="eyebrow">IdeaHacks Bookshelf</p>
					<h1>Live e-reader dock</h1>
					<p className="lede">
						This tiny frontend connects to the Flask WebSocket stream and shows
						what the backend sees through Calibre.
					</p>
				</div>

				<div className={`connection-card ${connectionState}`}>
					<span className="pulse" aria-hidden="true" />
					<div>
						<p className="label">Backend stream</p>
						<strong>{connectionState}</strong>
						<code>{streamUrl}</code>
					</div>
				</div>
			</section>

			<section className="reader-panel">
				<div className="reader-summary">
					<p className="label">Connected reader</p>
					<h2>{reader?.name ?? "No e-reader detected"}</h2>
					<p>
						{reader
							? `${books.length} book${books.length === 1 ? "" : "s"} reported by the backend.`
							: "Dock an e-reader to let the backend scan it with Calibre."}
					</p>
				</div>

				{featuredBooks.length > 0 ? (
					<ul className="book-grid">
						{featuredBooks.map((book, index) => (
							<li key={getBookKey(book)} className="book-card">
								<p className="book-index">
									{String(index + 1).padStart(2, "0")}
								</p>
								<h3>{book.title || "Untitled book"}</h3>
								<p className="author">
									{book.authors_display || "Unknown author"}
								</p>
								<div className="metadata">
									<span>{formatDate(book.pubdate)}</span>
									<span>{book.publisher || "Unknown publisher"}</span>
								</div>
								{book.series ? (
									<p className="series">
										{book.series}
										{book.series_index ? ` #${book.series_index}` : ""}
									</p>
								) : null}
							</li>
						))}
					</ul>
				) : (
					<div className="empty-state">
						<p className="shelf-mark">No books yet</p>
						<p>
							When the backend detects a reader, its book list will appear here
							without refreshing the page.
						</p>
					</div>
				)}

				<div className="backend-facts">
					<div>
						<p className="label">Backend capability</p>
						<strong>USB hotplug events</strong>
					</div>
					<div>
						<p className="label">Backend capability</p>
						<strong>Calibre device metadata</strong>
					</div>
					<div>
						<p className="label">Backend capability</p>
						<strong>Realtime WebSocket broadcast</strong>
					</div>
				</div>
			</section>
		</main>
	);
}

export default App;
