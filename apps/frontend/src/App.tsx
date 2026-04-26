import "./App.css";
import { useCallback, useEffect, useRef, useState } from "react";

type Book = {
	title?: string;
	authors?: string[];
	authors_display?: string;
	identifiers?: Record<string, string> | null;
	series?: string | null;
	series_index?: string | null;
	tags?: string[];
	languages?: string[];
	pubdate?: string | null;
	publisher?: string | null;
	path?: string | null;
	lpath?: string | null;
	size?: number | null;
	mime?: string | null;
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

type TransferJobMessage = {
	type: "transfer_job";
	job: TransferJob;
};

type StreamMessage = DeviceStateMessage | TransferJobMessage;

type ConnectionState = "connecting" | "connected" | "disconnected" | "error";
type TransferState = {
	busyKey: string | null;
	jobId: string | null;
	stage: string | null;
	progress: number | null;
	displayProgress: number | null;
	message: string | null;
	error: string | null;
	lastKey: string | null;
};

type JobResolver<T> = {
	busyKey: string;
	resolve: (result: T) => void;
	reject: (error: Error) => void;
	catchUpTimer: number;
	fallbackTimer: number;
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

const formatBookAuthors = (book: Book) => {
	if (book.authors_display) {
		return book.authors_display;
	}
	if (book.authors?.length) {
		return book.authors.join(" & ");
	}
	return "Unknown author";
};

const formatSize = (value?: number | null) => {
	if (!value) {
		return null;
	}
	if (value > 1024 * 1024) {
		return `${(value / 1024 / 1024).toFixed(1)} MB`;
	}
	if (value > 1024) {
		return `${Math.round(value / 1024)} KB`;
	}
	return `${value} B`;
};

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

const postForm = async <T,>(url: string, payload: FormData) => {
	const response = await fetch(url, {
		method: "POST",
		body: payload,
	});
	const decoded = (await response.json()) as T & { error?: string };
	if (!response.ok) {
		throw new Error(decoded.error || "Request failed");
	}
	return decoded;
};

function App() {
	const [connectionState, setConnectionState] =
		useState<ConnectionState>("connecting");
	const [reader, setReader] = useState<ConnectedEReader | null>(null);
	const [library, setLibrary] = useState<LibraryResponse["library"] | null>(
		null,
	);
	const [libraryBooks, setLibraryBooks] = useState<LibraryBook[]>([]);
	const [libraryQuery, setLibraryQuery] = useState("");
	const [selectedLibraryFiles, setSelectedLibraryFiles] = useState<File[]>([]);
	const [selectedLibraryBookIds, setSelectedLibraryBookIds] = useState<
		Set<number>
	>(() => new Set());
	const [selectedDeviceBookKeys, setSelectedDeviceBookKeys] = useState<
		Set<string>
	>(() => new Set());
	const [transferState, setTransferState] = useState<TransferState>({
		busyKey: null,
		jobId: null,
		stage: null,
		progress: null,
		displayProgress: null,
		message: null,
		error: null,
		lastKey: null,
	});
	const [apiBaseUrl] = useState(getBackendHttpUrl);
	const [streamUrl] = useState(getStreamUrl);
	const jobResolvers = useRef<Map<string, JobResolver<unknown>>>(new Map());
	const activeJobKeys = useRef<Map<string, string>>(new Map());
	const connectionStateRef = useRef<ConnectionState>("connecting");

	useEffect(() => {
		connectionStateRef.current = connectionState;
	}, [connectionState]);

	const loadLibrary = async (query = libraryQuery) => {
		const url = new URL(`${apiBaseUrl}/api/library`);
		if (query.trim()) {
			url.searchParams.set("query", query.trim());
		}
		const response = await fetch(url);
		const decoded = (await response.json()) as LibraryResponse & {
			error?: string;
		};
		if (!response.ok) {
			throw new Error(decoded.error || "Failed to load library");
		}
		setLibrary(decoded.library);
		setLibraryBooks(decoded.books);
	};

	useEffect(() => {
		const url = new URL(`${apiBaseUrl}/api/library`);
		void fetch(url)
			.then(async (response) => {
				const decoded = (await response.json()) as LibraryResponse & {
					error?: string;
				};
				if (!response.ok) {
					throw new Error(decoded.error || "Failed to load library");
				}
				setLibrary(decoded.library);
				setLibraryBooks(decoded.books);
			})
			.catch((error: unknown) => {
				setTransferState({
					busyKey: null,
					jobId: null,
					stage: null,
					progress: null,
					displayProgress: null,
					message: null,
					error: error instanceof Error ? error.message : String(error),
					lastKey: null,
				});
			});
	}, [apiBaseUrl]);

	const applyJobProgress = useCallback((busyKey: string, job: TransferJob) => {
		setTransferState({
			busyKey,
			jobId: job.id,
			stage: job.stage,
			progress: job.progress,
			displayProgress: job.progress,
			message: null,
			error: null,
			lastKey: null,
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
			if (job.status === "completed" && job.result) {
				if (resolver) {
					window.clearTimeout(resolver.catchUpTimer);
					window.clearTimeout(resolver.fallbackTimer);
					jobResolvers.current.delete(job.id);
					activeJobKeys.current.delete(job.id);
					resolver.resolve(job.result);
				}
			}
		},
		[applyJobProgress],
	);

	useEffect(() => {
		const socket = new WebSocket(streamUrl);

		socket.addEventListener("open", () => {
			setConnectionState("connected");
		});

		socket.addEventListener("message", (event) => {
			const message = JSON.parse(event.data) as StreamMessage;
			if ("job" in message) {
				handleStreamJob(message.job);
				return;
			}
			setReader(message.connected_e_reader);
		});

		socket.addEventListener("close", () => {
			setConnectionState((current) =>
				current === "connected" ? current : "disconnected",
			);
		});

		socket.addEventListener("error", () => {
			setConnectionState((current) =>
				current === "connected" ? current : "error",
			);
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

		if (connectionStateRef.current !== "connected") {
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
						if (!response.ok || !decoded.job) {
							return;
						}
						handleStreamJob(decoded.job);
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

	const runTransfer = async (
		busyKey: string,
		action: () => Promise<string>,
	) => {
		setTransferState({
			busyKey,
			jobId: null,
			stage: "Starting",
			progress: 0,
			displayProgress: 0,
			message: null,
			error: null,
			lastKey: null,
		});
		try {
			const message = await action();
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				displayProgress: null,
				message,
				error: null,
				lastKey: busyKey,
			});
		} catch (error) {
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				displayProgress: null,
				message: null,
				error: error instanceof Error ? error.message : String(error),
				lastKey: busyKey,
			});
		}
	};

	const sendToReader = (selectedBooks: LibraryBook[]) => {
		if (selectedBooks.length === 0) {
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				displayProgress: null,
				message: null,
				error: "Choose at least one library book first.",
				lastKey: null,
			});
			return;
		}

		const busyKey = `send-${selectedBooks.map((book) => book.id).join("-")}`;
		void runTransfer(busyKey, async () => {
			const response = await postJson<JobStartResponse<DeviceResponse>>(
				`${apiBaseUrl}/api/device/send`,
				{ book_ids: selectedBooks.map((book) => book.id) },
			);
			const result = await waitForJob(busyKey, response.job);
			setReader(result.connected_e_reader);
			setSelectedLibraryBookIds(new Set());
			return `Sent ${selectedBooks.length} book${
				selectedBooks.length === 1 ? "" : "s"
			} to the reader.`;
		});
	};

	const importFromReader = (selectedBooks: Book[]) => {
		const booksForImport = selectedBooks
			.map((book, index) => ({
				book,
				key: getBookKey(book, index),
				devicePath: getDevicePath(book),
			}))
			.filter((item): item is { book: Book; key: string; devicePath: string } =>
				Boolean(item.devicePath),
			);
		if (booksForImport.length === 0) {
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				displayProgress: null,
				message: null,
				error: "Choose at least one device book with a Calibre path first.",
				lastKey: null,
			});
			return;
		}

		const busyKey = `import-${booksForImport.map((item) => item.key).join("-")}`;
		void runTransfer(busyKey, async () => {
			const response = await postJson<
				JobStartResponse<{ books?: LibraryBook[] } & DeviceResponse>
			>(`${apiBaseUrl}/api/device/import`, {
				books: booksForImport.map((item) => ({
					device_path: item.devicePath,
					metadata: item.book,
				})),
			});
			const result = await waitForJob(busyKey, response.job);
			setReader(result.connected_e_reader);
			if (result.books) {
				setLibraryBooks(result.books);
			} else {
				await loadLibrary();
			}
			setSelectedDeviceBookKeys(new Set());
			return `Copied ${booksForImport.length} book${
				booksForImport.length === 1 ? "" : "s"
			} into the kiosk library.`;
		});
	};

	const deleteFromLibrary = (selectedBooks: LibraryBook[]) => {
		if (selectedBooks.length === 0) {
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				displayProgress: null,
				message: null,
				error: "Choose at least one library book first.",
				lastKey: null,
			});
			return;
		}

		const busyKey = `library-delete-${selectedBooks.map((book) => book.id).join("-")}`;
		void runTransfer(busyKey, async () => {
			const response = await postJson<JobStartResponse<LibraryResponse>>(
				`${apiBaseUrl}/api/library/delete`,
				{ book_ids: selectedBooks.map((book) => book.id) },
			);
			const result = await waitForJob(busyKey, response.job);
			setLibrary(result.library);
			setLibraryBooks(result.books);
			setSelectedLibraryBookIds(new Set());
			return `Deleted ${selectedBooks.length} book${
				selectedBooks.length === 1 ? "" : "s"
			} from the kiosk library.`;
		});
	};

	const importSelectedFilesToLibrary = () => {
		if (selectedLibraryFiles.length === 0) {
			setTransferState({
				busyKey: null,
				jobId: null,
				stage: null,
				progress: null,
				displayProgress: null,
				message: null,
				error: "Choose at least one book file first.",
				lastKey: null,
			});
			return;
		}

		void runTransfer("library-upload", async () => {
			const formData = new FormData();
			for (const file of selectedLibraryFiles) {
				formData.append("files", file);
			}
			const response = await postForm<LibraryResponse>(
				`${apiBaseUrl}/api/library/import`,
				formData,
			);
			setLibrary(response.library);
			setLibraryBooks(response.books);
			setSelectedLibraryFiles([]);
			return `Imported ${selectedLibraryFiles.length} book${
				selectedLibraryFiles.length === 1 ? "" : "s"
			} into the kiosk library.`;
		});
	};

	const books = reader?.books ?? [];
	const selectedDeviceBooks = books.filter((book, index) =>
		selectedDeviceBookKeys.has(getBookKey(book, index)),
	);
	const selectedLibraryBooks = libraryBooks.filter((book) =>
		selectedLibraryBookIds.has(book.id),
	);
	const toggleDeviceBook = (book: Book, index: number) => {
		const key = getBookKey(book, index);
		setSelectedDeviceBookKeys((current) => {
			const next = new Set(current);
			if (next.has(key)) {
				next.delete(key);
			} else {
				next.add(key);
			}
			return next;
		});
	};
	const toggleLibraryBook = (bookId: number) => {
		setSelectedLibraryBookIds((current) => {
			const next = new Set(current);
			if (next.has(bookId)) {
				next.delete(bookId);
			} else {
				next.add(bookId);
			}
			return next;
		});
	};

	useEffect(() => {
		if (!transferState.busyKey || transferState.progress === null) {
			return;
		}

		const timer = window.setInterval(() => {
			setTransferState((current) => {
				if (!current.busyKey || current.progress === null) {
					return current;
				}
				const displayed = current.displayProgress ?? current.progress;
				const ceiling = Math.min(0.97, current.progress + 0.08);
				if (displayed >= ceiling) {
					return current;
				}
				return {
					...current,
					displayProgress: Math.min(ceiling, displayed + 0.006),
				};
			});
		}, 180);

		return () => window.clearInterval(timer);
	}, [transferState.busyKey, transferState.progress]);

	return (
		<main className="kiosk-shell">
			<section className="status-panel">
				<div>
					<p className="eyebrow">IdeaHacks Bookshelf</p>
					<h1>Calibre-powered book dock</h1>
					<p className="lede">
						Copy books between a kiosk Calibre library and a docked e-reader,
						then manage the kiosk library with Calibre-backed actions.
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

			{transferState.message || transferState.error ? (
				<section
					className={`notice-panel ${transferState.error ? "error" : "success"}`}
				>
					{transferState.error ?? transferState.message}
				</section>
			) : null}

			{transferState.busyKey && transferState.stage ? (
				<section className="notice-panel">
					<div className="progress-copy">
						<span>{transferState.stage}</span>
						{typeof transferState.progress === "number" ? (
							<strong>{Math.round(transferState.progress * 100)}%</strong>
						) : null}
					</div>
					{typeof transferState.displayProgress === "number" ? (
						<div
							className="progress-track"
							aria-label="Transfer progress"
							aria-valuemin={0}
							aria-valuemax={100}
							aria-valuenow={Math.round(transferState.progress ?? 0)}
							role="progressbar"
						>
							<div
								className="progress-fill"
								style={{
									width: `${Math.round(transferState.displayProgress * 100)}%`,
								}}
							/>
						</div>
					) : null}
				</section>
			) : null}

			<section className="library-tools">
				<div>
					<p className="label">Kiosk Calibre library</p>
					<strong>{libraryBooks.length} books available</strong>
					<code>{library?.path ?? "Loading library path..."}</code>
				</div>
				<div className="library-actions">
					<form
						className="search-form"
						onSubmit={(event) => {
							event.preventDefault();
							void loadLibrary().catch((error: unknown) => {
								setTransferState({
									busyKey: null,
									jobId: null,
									stage: null,
									progress: null,
									displayProgress: null,
									message: null,
									error: error instanceof Error ? error.message : String(error),
									lastKey: null,
								});
							});
						}}
					>
						<input
							value={libraryQuery}
							onChange={(event) => setLibraryQuery(event.target.value)}
							placeholder='Search Calibre, e.g. tag:fiction or author:"Le Guin"'
						/>
						<button type="submit">Search</button>
						<button
							type="button"
							onClick={() => {
								setLibraryQuery("");
								void loadLibrary("").catch((error: unknown) => {
									setTransferState({
										busyKey: null,
										jobId: null,
										stage: null,
										progress: null,
										displayProgress: null,
										message: null,
										error:
											error instanceof Error ? error.message : String(error),
										lastKey: null,
									});
								});
							}}
						>
							Reset
						</button>
					</form>
					<form
						className="upload-form"
						onSubmit={(event) => {
							event.preventDefault();
							importSelectedFilesToLibrary();
						}}
					>
						<label className="file-picker">
							<span>
								{selectedLibraryFiles.length
									? `${selectedLibraryFiles.length} file${
											selectedLibraryFiles.length === 1 ? "" : "s"
										} selected`
									: "Choose book files"}
							</span>
							<input
								type="file"
								multiple
								accept=".epub,.mobi,.azw,.azw3,.kfx,.pdf,.txt,.cbz,.cbr"
								onChange={(event) =>
									setSelectedLibraryFiles(
										Array.from(event.currentTarget.files ?? []),
									)
								}
							/>
						</label>
						<button
							type="submit"
							disabled={
								transferState.busyKey !== null ||
								selectedLibraryFiles.length === 0
							}
						>
							Import to library
						</button>
					</form>
				</div>
			</section>

			<section className="transfer-layout">
				<section className="reader-panel">
					<div className="reader-summary">
						<p className="label">Connected reader</p>
						<h2>{reader?.name ?? "No e-reader detected"}</h2>
						<p>
							{reader
								? `${books.length} book${books.length === 1 ? "" : "s"} reported by Calibre.`
								: "Dock an e-reader to scan it and enable transfer actions."}
						</p>
						{books.length > 0 ? (
							<div className="selection-bar">
								<strong>{selectedDeviceBooks.length} selected</strong>
								<button
									type="button"
									disabled={transferState.busyKey !== null}
									onClick={() => importFromReader(selectedDeviceBooks)}
								>
									Copy selected
								</button>
							</div>
						) : null}
					</div>

					{books.length > 0 ? (
						<ul className="book-grid compact">
							{books.map((book, index) => (
								<DeviceBookCard
									key={getBookKey(book, index)}
									book={book}
									index={index}
									selected={selectedDeviceBookKeys.has(getBookKey(book, index))}
									apiBaseUrl={apiBaseUrl}
									transferState={transferState}
									onToggle={() => toggleDeviceBook(book, index)}
									onImport={(bookToImport) => importFromReader([bookToImport])}
								/>
							))}
						</ul>
					) : (
						<EmptyState
							title="No device books yet"
							body="When the backend detects a reader, its book list and import controls appear here."
						/>
					)}
				</section>

				<section className="reader-panel library-panel">
					<div className="reader-summary">
						<p className="label">Kiosk library</p>
						<h2>Send books to reader</h2>
						<p>
							{library?.metadata_db_exists
								? "Books come from the configured Calibre library."
								: "No Calibre metadata.db exists yet. Import a reader book or add books with Calibre to initialize it."}
						</p>
						{libraryBooks.length > 0 ? (
							<div className="selection-bar">
								<strong>{selectedLibraryBooks.length} selected</strong>
								<button
									type="button"
									disabled={transferState.busyKey !== null || !reader}
									onClick={() => sendToReader(selectedLibraryBooks)}
								>
									Send selected
								</button>
								<button
									type="button"
									className="danger"
									disabled={transferState.busyKey !== null}
									onClick={() => deleteFromLibrary(selectedLibraryBooks)}
								>
									Delete selected
								</button>
							</div>
						) : null}
					</div>

					{libraryBooks.length > 0 ? (
						<ul className="book-grid compact">
							{libraryBooks.map((book, index) => (
								<li
									key={book.id}
									className={`book-card library-book ${
										selectedLibraryBookIds.has(book.id) ? "selected" : ""
									} ${
										transferState.lastKey === `send-${book.id}` ||
										transferState.lastKey === `library-delete-${book.id}`
											? "complete"
											: ""
									}`}
								>
									<label className="select-row">
										<input
											type="checkbox"
											checked={selectedLibraryBookIds.has(book.id)}
											onChange={() => toggleLibraryBook(book.id)}
										/>
										<span>Select</span>
									</label>
									<BookCardContent
										book={book}
										index={index}
										apiBaseUrl={apiBaseUrl}
									/>
									<div className="formats">
										{book.formats.length
											? book.formats.map((format) => (
													<span key={format}>{format}</span>
												))
											: "No formats"}
									</div>
									<div className="card-actions">
										<button
											type="button"
											disabled={
												transferState.busyKey !== null ||
												!reader ||
												book.formats.length === 0
											}
											onClick={() => sendToReader([book])}
										>
											{transferState.busyKey === `send-${book.id}`
												? "Sending..."
												: "Send to reader"}
										</button>
										<button
											type="button"
											className="danger"
											disabled={transferState.busyKey !== null}
											onClick={() => deleteFromLibrary([book])}
										>
											{transferState.busyKey === `library-delete-${book.id}`
												? "Deleting..."
												: "Delete"}
										</button>
									</div>
									{transferState.lastKey === `send-${book.id}` ? (
										<p className="inline-status">Sent to reader.</p>
									) : null}
								</li>
							))}
						</ul>
					) : (
						<EmptyState
							title="No kiosk books yet"
							body="Add books to the configured Calibre library or copy books from the connected reader."
						/>
					)}
				</section>
			</section>
		</main>
	);
}

function DeviceBookCard({
	book,
	index,
	selected,
	apiBaseUrl,
	transferState,
	onToggle,
	onImport,
}: {
	book: Book;
	index: number;
	selected: boolean;
	apiBaseUrl: string;
	transferState: TransferState;
	onToggle: () => void;
	onImport: (book: Book) => void;
}) {
	const devicePath = getDevicePath(book) ?? getBookKey(book, index);
	const copyKey = `import-${devicePath}`;
	const completed = transferState.lastKey === copyKey;

	return (
		<li
			className={`book-card device-book ${selected ? "selected" : ""} ${
				completed ? "complete" : ""
			}`}
		>
			<label className="select-row">
				<input type="checkbox" checked={selected} onChange={onToggle} />
				<span>Select</span>
			</label>
			<BookCardContent book={book} index={index} apiBaseUrl={apiBaseUrl} />
			<div className="card-actions">
				<button
					type="button"
					disabled={transferState.busyKey !== null}
					onClick={() => onImport(book)}
				>
					{transferState.busyKey === copyKey ? "Copying..." : "Copy to kiosk"}
				</button>
			</div>
			{completed ? <p className="inline-status">Library updated.</p> : null}
		</li>
	);
}

function BookCardContent({
	book,
	index,
	apiBaseUrl,
}: {
	book: Book;
	index: number;
	apiBaseUrl: string;
}) {
	const size = formatSize(book.size);
	const coverUrl = getCoverUrl(book, apiBaseUrl);

	return (
		<>
			<div className="cover-frame">
				{coverUrl ? (
					<img src={coverUrl} alt="" loading="lazy" />
				) : (
					<div className="cover-placeholder" />
				)}
			</div>
			<p className="book-index">{String(index + 1).padStart(2, "0")}</p>
			<h3>{book.title || "Untitled book"}</h3>
			<p className="author">{formatBookAuthors(book)}</p>
			<div className="metadata">
				<span>{formatDate(book.pubdate)}</span>
				<span>{book.publisher || "Unknown publisher"}</span>
				{size ? <span>{size}</span> : null}
			</div>
			{book.series ? (
				<p className="series">
					{book.series}
					{book.series_index ? ` #${book.series_index}` : ""}
				</p>
			) : null}
		</>
	);
}

function EmptyState({ title, body }: { title: string; body: string }) {
	return (
		<div className="empty-state">
			<p className="shelf-mark">{title}</p>
			<p>{body}</p>
		</div>
	);
}

export default App;
