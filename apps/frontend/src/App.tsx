import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";

type Book = {
	title?: string;
	authors?: string[];
	authors_display?: string;
	identifiers?: Record<string, string> | null;
	series?: string | null;
	series_index?: string | null;
	pubdate?: string | null;
	publisher?: string | null;
	path?: string | null;
	lpath?: string | null;
	size?: number | null;
	cover_url?: string | null;
	cover_token?: string | null;
};

type LibraryBook = Book & {
	id: number;
	formats: string[];
};

type ConnectedEReader = {
	name: string;
	books: Book[];
};

type LibraryResponse = {
	library: {
		path: string;
		exists: boolean;
		metadata_db_exists: boolean;
	};
	books: LibraryBook[];
};

type DeviceResponse = {
	connected_e_reader: ConnectedEReader | null;
};

type TransferJob<T = unknown> = {
	id: string;
	kind: string;
	status: "queued" | "running" | "completed" | "failed";
	stage: string;
	progress: number;
	message: string | null;
	error: string | null;
	result: T | null;
};

type JobStartResponse<T> = {
	job: TransferJob<T>;
};

type DeviceStateMessage = {
	type?: "device_state";
	connected_e_reader: ConnectedEReader | null;
};

type DeviceRefreshMessage = {
	type: "device_refresh";
	status: "loading";
};

type TransferJobMessage = {
	type: "transfer_job";
	job: TransferJob;
};

type StreamMessage =
	| DeviceStateMessage
	| DeviceRefreshMessage
	| TransferJobMessage;

type TransferState = {
	busyKey: string | null;
	jobId: string | null;
	stage: string | null;
	progress: number | null;
	error: string | null;
};

type JobResolver<T> = {
	busyKey: string;
	resolve: (result: T) => void;
	reject: (error: Error) => void;
	catchUpTimer: number;
	fallbackTimer: number;
};

type DragPayload =
	| {
			source: "kiosk";
			bookId: number;
	  }
	| {
			source: "reader";
			bookKey: string;
	  };

type RailBook =
	| {
			source: "kiosk";
			book: LibraryBook;
			key: string;
	  }
	| {
			source: "reader";
			book: Book;
			key: string;
	  };

const getBackendHttpUrl = () => {
	const configuredBackendUrl = import.meta.env.VITE_BACKEND_URL as
		| string
		| undefined;
	if (configuredBackendUrl) {
		return configuredBackendUrl.replace(/\/$/, "");
	}

	if (window.location.port === "5005") {
		return window.location.origin;
	}

	return `${window.location.protocol}//localhost:5005`;
};

const getStreamUrl = () => {
	const backendUrl = new URL(getBackendHttpUrl());
	backendUrl.protocol = backendUrl.protocol === "https:" ? "wss:" : "ws:";
	backendUrl.pathname = "/stream";
	return backendUrl.toString();
};

const getBookKey = (book: Book, fallback: string | number) =>
	book.path ??
	book.lpath ??
	(book.identifiers ? JSON.stringify(book.identifiers) : undefined) ??
	[book.title, book.authors_display, book.publisher, book.pubdate, fallback]
		.filter(Boolean)
		.join("|");

const getDevicePath = (book: Book) => book.path ?? book.lpath ?? null;

const getCoverUrl = (book: Book, apiBaseUrl: string) => {
	if (!book.cover_url) {
		return null;
	}
	return new URL(book.cover_url, apiBaseUrl).toString();
};

const getBookTitle = (book: Book) => book.title || "Untitled book";

const getBookAuthors = (book: Book) => {
	if (book.authors_display) {
		return book.authors_display;
	}
	if (book.authors?.length) {
		return book.authors.join(" & ");
	}
	return "Unknown author";
};

const postJson = async <T,>(url: string, payload: Record<string, unknown>) => {
	const response = await fetch(url, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(payload),
	});
	const decoded = (await response.json()) as T & { error?: string };
	if (!response.ok) {
		throw new Error(decoded.error || "Request failed");
	}
	return decoded;
};

function App() {
	const [reader, setReader] = useState<ConnectedEReader | null>(null);
	const [deviceLoading, setDeviceLoading] = useState(false);
	const [libraryBooks, setLibraryBooks] = useState<LibraryBook[]>([]);
	const [currentTime, setCurrentTime] = useState(() => new Date());
	const [dragTarget, setDragTarget] = useState<"kiosk" | "reader" | null>(null);
	const [transferState, setTransferState] = useState<TransferState>({
		busyKey: null,
		jobId: null,
		stage: null,
		progress: null,
		error: null,
	});
	const [apiBaseUrl] = useState(getBackendHttpUrl);
	const [streamUrl] = useState(getStreamUrl);
	const jobResolvers = useRef<Map<string, JobResolver<unknown>>>(new Map());
	const activeJobKeys = useRef<Map<string, string>>(new Map());
	const streamConnectedRef = useRef(false);

	const readerBooks = useMemo(() => reader?.books ?? [], [reader]);
	const mode = reader || deviceLoading ? "active" : "idle";

	useEffect(() => {
		let timer: number;

		const scheduleNextMinute = () => {
			const now = new Date();
			const millisecondsUntilNextMinute =
				(60 - now.getSeconds()) * 1000 - now.getMilliseconds();

			timer = window.setTimeout(() => {
				setCurrentTime(new Date());
				scheduleNextMinute();
			}, millisecondsUntilNextMinute);
		};

		scheduleNextMinute();
		return () => window.clearTimeout(timer);
	}, []);

	const loadLibrary = useCallback(async () => {
		const response = await fetch(`${apiBaseUrl}/api/library`);
		const decoded = (await response.json()) as LibraryResponse & {
			error?: string;
		};
		if (!response.ok) {
			throw new Error(decoded.error || "Failed to load kiosk library");
		}
		setLibraryBooks(decoded.books);
	}, [apiBaseUrl]);

	useEffect(() => {
		const timer = window.setTimeout(() => {
			void loadLibrary().catch((error: unknown) => {
				setTransferState((current) => ({
					...current,
					error: error instanceof Error ? error.message : String(error),
				}));
			});
		}, 0);
		return () => window.clearTimeout(timer);
	}, [loadLibrary]);

	const applyJobProgress = useCallback((busyKey: string, job: TransferJob) => {
		setTransferState({
			busyKey,
			jobId: job.id,
			stage: job.stage,
			progress: job.progress,
			error: job.error,
		});
	}, []);

	const handleStreamJob = useCallback(
		(job: TransferJob) => {
			const resolver = jobResolvers.current.get(job.id);
			const busyKey = resolver?.busyKey ?? activeJobKeys.current.get(job.id);
			if (!busyKey) {
				return;
			}

			applyJobProgress(busyKey, job);
			if (job.status === "failed") {
				if (resolver) {
					window.clearTimeout(resolver.catchUpTimer);
					window.clearTimeout(resolver.fallbackTimer);
					jobResolvers.current.delete(job.id);
					activeJobKeys.current.delete(job.id);
					resolver.reject(new Error(job.error || "Transfer failed"));
				}
				return;
			}
			if (job.status === "completed" && job.result && resolver) {
				window.clearTimeout(resolver.catchUpTimer);
				window.clearTimeout(resolver.fallbackTimer);
				jobResolvers.current.delete(job.id);
				activeJobKeys.current.delete(job.id);
				resolver.resolve(job.result);
			}
		},
		[applyJobProgress],
	);

	useEffect(() => {
		const socket = new WebSocket(streamUrl);

		socket.addEventListener("open", () => {
			streamConnectedRef.current = true;
		});

		socket.addEventListener("message", (event) => {
			const message = JSON.parse(event.data) as StreamMessage;
			if (message.type === "transfer_job") {
				handleStreamJob(message.job);
				return;
			}
			if (message.type === "device_refresh") {
				setDeviceLoading(true);
				return;
			}
			setReader(message.connected_e_reader);
			setDeviceLoading(false);
		});

		socket.addEventListener("close", () => {
			streamConnectedRef.current = false;
		});

		socket.addEventListener("error", () => {
			streamConnectedRef.current = false;
		});

		return () => {
			socket.close();
		};
	}, [streamUrl, handleStreamJob]);

	const pollJobUntilComplete = async <T,>(busyKey: string, jobId: string) => {
		while (true) {
			const response = await fetch(`${apiBaseUrl}/api/jobs/${jobId}`);
			const decoded = (await response.json()) as {
				job?: TransferJob<T>;
				error?: string;
			};
			if (!response.ok || !decoded.job) {
				throw new Error(decoded.error || "Failed to check transfer job");
			}

			const job = decoded.job;
			applyJobProgress(busyKey, job);
			if (job.status === "failed") {
				throw new Error(job.error || "Transfer failed");
			}
			if (job.status === "completed") {
				if (!job.result) {
					throw new Error("Transfer finished without a result");
				}
				return job.result;
			}
			await new Promise((resolve) => window.setTimeout(resolve, 1000));
		}
	};

	const waitForJob = async <T,>(
		busyKey: string,
		startedJob: TransferJob<T>,
	) => {
		applyJobProgress(busyKey, startedJob);
		activeJobKeys.current.set(startedJob.id, busyKey);

		if (startedJob.status === "failed") {
			activeJobKeys.current.delete(startedJob.id);
			throw new Error(startedJob.error || "Transfer failed");
		}
		if (startedJob.status === "completed") {
			activeJobKeys.current.delete(startedJob.id);
			if (!startedJob.result) {
				throw new Error("Transfer finished without a result");
			}
			return startedJob.result;
		}

		if (!streamConnectedRef.current) {
			try {
				return await pollJobUntilComplete<T>(busyKey, startedJob.id);
			} finally {
				activeJobKeys.current.delete(startedJob.id);
			}
		}

		return await new Promise<T>((resolve, reject) => {
			const catchUpTimer = window.setTimeout(() => {
				if (!jobResolvers.current.has(startedJob.id)) {
					return;
				}
				void fetch(`${apiBaseUrl}/api/jobs/${startedJob.id}`)
					.then(async (response) => {
						const decoded = (await response.json()) as {
							job?: TransferJob<T>;
							error?: string;
						};
						if (response.ok && decoded.job) {
							handleStreamJob(decoded.job);
						}
					})
					.catch(() => undefined);
			}, 750);
			const fallbackTimer = window.setTimeout(() => {
				window.clearTimeout(catchUpTimer);
				jobResolvers.current.delete(startedJob.id);
				void pollJobUntilComplete<T>(busyKey, startedJob.id)
					.then(resolve)
					.catch(reject)
					.finally(() => activeJobKeys.current.delete(startedJob.id));
			}, 30000);

			jobResolvers.current.set(startedJob.id, {
				busyKey,
				resolve: resolve as (result: unknown) => void,
				reject,
				catchUpTimer,
				fallbackTimer,
			});
		});
	};

	const runTransfer = async (busyKey: string, action: () => Promise<void>) => {
		setTransferState({
			busyKey,
			jobId: null,
			stage: "Starting",
			progress: 0,
			error: null,
		});
		try {
			await action();
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				error: null,
			});
		} catch (error) {
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				error: error instanceof Error ? error.message : String(error),
			});
		}
	};

	const sendToReader = (book: LibraryBook) => {
		if (!reader) {
			return;
		}

		const busyKey = `send-${book.id}`;
		void runTransfer(busyKey, async () => {
			const response = await postJson<JobStartResponse<DeviceResponse>>(
				`${apiBaseUrl}/api/device/send`,
				{ book_id: book.id },
			);
			const result = await waitForJob<DeviceResponse>(busyKey, response.job);
			setReader(result.connected_e_reader);
		});
	};

	const importFromReader = (book: Book, key: string) => {
		const devicePath = getDevicePath(book);
		if (!devicePath) {
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				error: "This reader book does not include a transfer path.",
			});
			return;
		}

		const busyKey = `import-${key}`;
		void runTransfer(busyKey, async () => {
			const response = await postJson<
				JobStartResponse<{ books?: LibraryBook[] } & DeviceResponse>
			>(`${apiBaseUrl}/api/device/import`, {
				books: [{ device_path: devicePath, metadata: book }],
			});
			const result = await waitForJob<
				{ books?: LibraryBook[] } & DeviceResponse
			>(busyKey, response.job);
			setReader(result.connected_e_reader);
			if (result.books) {
				setLibraryBooks(result.books);
			} else {
				await loadLibrary();
			}
		});
	};

	const libraryRailBooks = useMemo<RailBook[]>(
		() =>
			libraryBooks.map((book) => ({
				source: "kiosk",
				book,
				key: String(book.id),
			})),
		[libraryBooks],
	);

	const readerRailBooks = useMemo<RailBook[]>(
		() =>
			readerBooks.map((book, index) => ({
				source: "reader",
				book,
				key: getBookKey(book, index),
			})),
		[readerBooks],
	);

	const handleDrop = (
		destination: "kiosk" | "reader",
		payload: DragPayload,
	) => {
		setDragTarget(null);
		if (transferState.busyKey) {
			return;
		}
		if (destination === "reader" && payload.source === "kiosk") {
			const book = libraryBooks.find(
				(candidate) => candidate.id === payload.bookId,
			);
			if (book) {
				sendToReader(book);
			}
			return;
		}
		if (destination === "kiosk" && payload.source === "reader") {
			const item = readerRailBooks.find(
				(candidate) => candidate.key === payload.bookKey,
			);
			if (item) {
				importFromReader(item.book, item.key);
			}
		}
	};

	const timeLabel = currentTime.toLocaleTimeString([], {
		hour: "numeric",
		minute: "2-digit",
	});

	return (
		<main className={`pibrary-kiosk ${mode}`}>
			<div className="wood-wash" />
			<section className="idle-clock" aria-label="Current time">
				<p>
					pibrary <em>β</em>
				</p>
				<time dateTime={currentTime.toISOString()}>{timeLabel}</time>
			</section>

			<section
				className={`reader-board ${deviceLoading ? "loading" : ""}`}
				aria-label="Connected e-reader"
			>
				{deviceLoading && !reader ? (
					<div className="reader-loading">Loading e-reader inventory...</div>
				) : (
					<BookRail
						apiBaseUrl={apiBaseUrl}
						books={readerRailBooks}
						destination="reader"
						dragTarget={dragTarget}
						emptyLabel="No reader books"
						onDragTarget={setDragTarget}
						onDropBook={handleDrop}
						transferState={transferState}
					/>
				)}
			</section>

			<section className="library-board" aria-label="Kiosk books">
				<BookRail
					apiBaseUrl={apiBaseUrl}
					books={libraryRailBooks}
					destination="kiosk"
					dragTarget={dragTarget}
					emptyLabel="No kiosk books"
					onDragTarget={setDragTarget}
					onDropBook={handleDrop}
					transferState={transferState}
				/>
			</section>

			{transferState.busyKey && transferState.stage ? (
				<div className="transfer-toast" role="status">
					<span>{transferState.stage}</span>
					{typeof transferState.progress === "number" ? (
						<strong>{Math.round(transferState.progress * 100)}%</strong>
					) : null}
				</div>
			) : null}

			{transferState.error ? (
				<div className="transfer-toast error" role="alert">
					{transferState.error}
				</div>
			) : null}
		</main>
	);
}

function BookRail({
	apiBaseUrl,
	books,
	destination,
	dragTarget,
	emptyLabel,
	onDragTarget,
	onDropBook,
	transferState,
}: {
	apiBaseUrl: string;
	books: RailBook[];
	destination: "kiosk" | "reader";
	dragTarget: "kiosk" | "reader" | null;
	emptyLabel: string;
	onDragTarget: (target: "kiosk" | "reader" | null) => void;
	onDropBook: (destination: "kiosk" | "reader", payload: DragPayload) => void;
	transferState: TransferState;
}) {
	const [query, setQuery] = useState("");
	const [searchOpen, setSearchOpen] = useState(false);
	const normalizedQuery = query.trim().toLowerCase();
	const filteredBooks = normalizedQuery
		? books.filter((item) =>
				[getBookTitle(item.book), getBookAuthors(item.book)]
					.join(" ")
					.toLowerCase()
					.includes(normalizedQuery),
			)
		: books;
	const canReceive =
		(destination === "kiosk" && dragTarget === "kiosk") ||
		(destination === "reader" && dragTarget === "reader");
	const placeholderCount = Math.max(
		0,
		4 - filteredBooks.length - (canReceive ? 1 : 0),
	);
	const placeholderSlots = ["one", "two", "three", "four"].slice(
		0,
		placeholderCount,
	);

	const parseDragPayload = (event: React.DragEvent) => {
		const encoded = event.dataTransfer.getData("application/x-pibrary-book");
		if (!encoded) {
			return null;
		}
		try {
			return JSON.parse(encoded) as DragPayload;
		} catch {
			return null;
		}
	};

	const acceptsPayload = (payload: DragPayload | null) =>
		Boolean(
			payload &&
				((destination === "kiosk" && payload.source === "reader") ||
					(destination === "reader" && payload.source === "kiosk")),
		);

	return (
		<section
			className={`book-rail-drop ${canReceive ? "can-receive" : ""}`}
			aria-label={`${destination} drop target`}
			onDragEnter={(event) => {
				if (acceptsPayload(parseDragPayload(event))) {
					onDragTarget(destination);
					navigator.vibrate?.(18);
				}
			}}
			onDragOver={(event) => {
				if (acceptsPayload(parseDragPayload(event))) {
					event.preventDefault();
					event.dataTransfer.dropEffect = "copy";
					onDragTarget(destination);
				}
			}}
			onDragLeave={(event) => {
				if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
					onDragTarget(null);
				}
			}}
			onDrop={(event) => {
				const payload = parseDragPayload(event);
				if (!acceptsPayload(payload)) {
					onDragTarget(null);
					return;
				}
				event.preventDefault();
				if (payload) {
					navigator.vibrate?.([22, 18, 22]);
					onDropBook(destination, payload);
				}
			}}
		>
			<div className="book-rail">
				<SearchCard
					open={searchOpen}
					query={query}
					onOpen={() => setSearchOpen(true)}
					onQueryChange={setQuery}
					onReset={() => {
						setQuery("");
						setSearchOpen(false);
					}}
				/>
				{canReceive ? <div className="drop-placeholder" /> : null}
				{filteredBooks.map((item) => (
					<BookCoverCard
						apiBaseUrl={apiBaseUrl}
						item={item}
						key={`${item.source}-${item.key}`}
						transferState={transferState}
					/>
				))}
				{placeholderSlots.map((slot, index) => (
					<div
						className="empty-book-card"
						key={`${destination}-placeholder-${slot}`}
						title={
							filteredBooks.length === 0 && index === 0
								? normalizedQuery
									? "No matches"
									: emptyLabel
								: undefined
						}
					/>
				))}
			</div>
		</section>
	);
}

function SearchCard({
	open,
	query,
	onOpen,
	onQueryChange,
	onReset,
}: {
	open: boolean;
	query: string;
	onOpen: () => void;
	onQueryChange: (value: string) => void;
	onReset: () => void;
}) {
	return (
		<div className={`search-card ${open ? "open" : ""}`}>
			<button
				type="button"
				className="search-trigger"
				aria-label="Search books"
				onClick={onOpen}
			>
				<span className="magnifier" aria-hidden="true" />
				<span>Search...</span>
			</button>
			{open ? (
				<form
					className="search-form"
					onSubmit={(event) => {
						event.preventDefault();
					}}
				>
					<input
						aria-label="Search books"
						value={query}
						onChange={(event) => onQueryChange(event.currentTarget.value)}
					/>
					<button type="button" onClick={onReset} aria-label="Clear search">
						Clear
					</button>
				</form>
			) : null}
		</div>
	);
}

function BookCoverCard({
	apiBaseUrl,
	item,
	transferState,
}: {
	apiBaseUrl: string;
	item: RailBook;
	transferState: TransferState;
}) {
	const coverUrl = getCoverUrl(item.book, apiBaseUrl);
	const title = getBookTitle(item.book);
	const authors = getBookAuthors(item.book);
	const busyKey =
		item.source === "kiosk" ? `send-${item.book.id}` : `import-${item.key}`;
	const isBusy = transferState.busyKey === busyKey;

	const payload: DragPayload =
		item.source === "kiosk"
			? { source: "kiosk", bookId: item.book.id }
			: { source: "reader", bookKey: item.key };

	return (
		<article
			className={`book-cover-card ${isBusy ? "transferring" : ""}`}
			draggable={!transferState.busyKey}
			onDragStart={(event) => {
				event.dataTransfer.effectAllowed = "copy";
				event.dataTransfer.setData(
					"application/x-pibrary-book",
					JSON.stringify(payload),
				);
			}}
			title={`${title} by ${authors}`}
		>
			{coverUrl ? (
				<img src={coverUrl} alt={`${title} cover`} loading="lazy" />
			) : (
				<div className="cover-fallback">
					<strong>{title}</strong>
					<span>{authors}</span>
				</div>
			)}
			{isBusy ? <div className="book-busy">Moving...</div> : null}
		</article>
	);
}

export default App;
