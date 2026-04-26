import "./App.css";

import {
	type ChangeEvent,
	type DragEvent,
	type KeyboardEvent,
	useCallback,
	useEffect,
	useMemo,
	useState,
} from "react";

type View = "home" | "library" | "transfer" | "manage";
type Source = "library" | "device";
type JobStatus = "queued" | "running" | "completed" | "failed";

type LibraryBook = {
	id: number;
	title: string;
	authors: string[];
	tags: string[];
	series: string | null;
	seriesIndex: number | null;
	publisher: string | null;
	languages: string[];
	identifiers: Record<string, string>;
	uuid: string | null;
	formats: string[];
	lastModified: string | null;
	publishedAt: string | null;
	coverUrl: string;
};

type DeviceBook = {
	id: string;
	storage: string;
	path: string;
	lpath: string;
	title: string;
	authors: string[];
	tags: string[];
	collections: string[];
	series: string | null;
	seriesIndex: number | null;
	size: number | null;
	mime: string | null;
	applicationId: string | null;
	libraryBookId: number | null;
};

type CollectionItem = {
	id: string;
	name: string;
	bookCount: number;
};

type LibraryCollections = {
	tags: CollectionItem[];
	series: CollectionItem[];
	authors: CollectionItem[];
};

type DeviceStatus = {
	connected: boolean;
	device: {
		name?: string;
		driver?: string;
		formats?: string[];
		supportsCollections?: boolean;
		[key: string]: unknown;
	} | null;
	lastError: string | null;
};

type TransferJob = {
	id: string;
	direction: "library-to-device" | "device-to-library";
	status: JobStatus;
	total: number;
	completed: number;
	error: string | null;
	result: Record<string, unknown> | null;
	createdAt: string;
	updatedAt: string;
};

type DragPayload =
	| { source: "library"; bookIds: number[] }
	| { source: "device"; paths: string[] };

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

const emptyCollections: LibraryCollections = {
	tags: [],
	series: [],
	authors: [],
};

const demoLibrary: LibraryBook[] = [
	{
		id: -1,
		title: "The Hobbit",
		authors: ["J.R.R. Tolkien"],
		tags: ["Favorites", "Adventure"],
		series: null,
		seriesIndex: null,
		publisher: "Family shelf",
		languages: ["eng"],
		identifiers: {},
		uuid: null,
		formats: ["EPUB"],
		lastModified: null,
		publishedAt: null,
		coverUrl: "",
	},
	{
		id: -2,
		title: "The Secret Garden",
		authors: ["Frances Hodgson Burnett"],
		tags: ["Classics", "Kids"],
		series: null,
		seriesIndex: null,
		publisher: "Family shelf",
		languages: ["eng"],
		identifiers: {},
		uuid: null,
		formats: ["EPUB"],
		lastModified: null,
		publishedAt: null,
		coverUrl: "",
	},
	{
		id: -3,
		title: "Pride and Prejudice",
		authors: ["Jane Austen"],
		tags: ["Classics"],
		series: null,
		seriesIndex: null,
		publisher: "Family shelf",
		languages: ["eng"],
		identifiers: {},
		uuid: null,
		formats: ["EPUB", "PDF"],
		lastModified: null,
		publishedAt: null,
		coverUrl: "",
	},
];

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(`${API_BASE}${path}`, {
		...init,
		headers:
			init?.body instanceof FormData
				? init.headers
				: {
						"Content-Type": "application/json",
						...init?.headers,
					},
	});

	if (!response.ok) {
		let message = response.statusText;
		try {
			const body = (await response.json()) as { error?: string };
			message = body.error ?? message;
		} catch {
			// Keep the status text when the backend returns an empty error body.
		}
		throw new Error(message);
	}

	if (response.status === 204) {
		return undefined as T;
	}

	return (await response.json()) as T;
}

function apiUrl(path: string): string {
	return `${API_BASE}${path}`;
}

function bookTone(title: string): number {
	let total = 0;
	for (const char of title) {
		total += char.charCodeAt(0);
	}
	return total % 6;
}

function byTitle<T extends { title: string }>(a: T, b: T): number {
	return a.title.localeCompare(b.title);
}

function humanList(items: string[]): string {
	return items.length ? items.join(", ") : "Unknown author";
}

function matchesBook(
	book: Pick<LibraryBook | DeviceBook, "title" | "authors" | "tags" | "series">,
	query: string,
): boolean {
	const normalized = query.trim().toLowerCase();
	if (!normalized) {
		return true;
	}
	const haystack = [
		book.title,
		...book.authors,
		...book.tags,
		book.series ?? "",
	].join(" ");
	return haystack.toLowerCase().includes(normalized);
}

function uniqueSorted(values: string[]): string[] {
	return [...new Set(values.filter(Boolean))].sort((a, b) =>
		a.localeCompare(b),
	);
}

function BookCover({
	book,
	source,
	size = "regular",
}: {
	book: LibraryBook | DeviceBook;
	source: Source;
	size?: "regular" | "small";
}) {
	const showImage = source === "library" && "coverUrl" in book && book.coverUrl;
	return (
		<div className={`book-cover tone-${bookTone(book.title)} ${size}`}>
			{showImage ? (
				<img
					src={apiUrl((book as LibraryBook).coverUrl)}
					alt=""
					loading="lazy"
				/>
			) : (
				<div className="cover-letterpress">
					<span>{book.title}</span>
					<small>{humanList(book.authors).split(",")[0]}</small>
				</div>
			)}
		</div>
	);
}

function BookCard({
	book,
	source,
	selected,
	onToggle,
	onDragStart,
	onOpen,
	compact = false,
}: {
	book: LibraryBook | DeviceBook;
	source: Source;
	selected: boolean;
	onToggle: () => void;
	onDragStart: (event: DragEvent<HTMLElement>) => void;
	onOpen?: () => void;
	compact?: boolean;
}) {
	const subtitle =
		source === "device" && "lpath" in book
			? book.lpath
			: humanList(book.authors);

	function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
		if (event.key === "Enter" || event.key === " ") {
			event.preventDefault();
			onToggle();
		}
	}

	return (
		<div className={`book-card-wrap ${compact ? "compact" : ""}`}>
			<button
				type="button"
				className={`book-card ${selected ? "selected" : ""}`}
				aria-pressed={selected}
				aria-label={`${book.title}. ${subtitle}. ${
					source === "library" && "formats" in book
						? book.formats.join(", ") || "No format"
						: "On e-reader"
				}.`}
				draggable
				onDragStart={onDragStart}
				onClick={onToggle}
				onKeyDown={handleKeyDown}
			>
				<BookCover
					book={book}
					source={source}
					size={compact ? "small" : "regular"}
				/>
				<div className="book-copy">
					<strong>{book.title}</strong>
					<span>{subtitle}</span>
					<small>
						{source === "library" && "formats" in book
							? book.formats.join(" / ") || "No format"
							: "On e-reader"}
					</small>
				</div>
			</button>
			{onOpen ? (
				<button
					type="button"
					className="text-button"
					onClick={(event) => {
						event.stopPropagation();
						onOpen();
					}}
				>
					Edit
				</button>
			) : null}
		</div>
	);
}

function DeviceFigure({
	status,
	bookCount,
	isDropTarget,
	onDrop,
	onDragOver,
}: {
	status: DeviceStatus;
	bookCount: number;
	isDropTarget?: boolean;
	onDrop?: (event: DragEvent<HTMLElement>) => void;
	onDragOver?: (event: DragEvent<HTMLElement>) => void;
}) {
	return (
		<section
			className={`ereader-figure ${status.connected ? "online" : "offline"} ${
				isDropTarget ? "drop-lit" : ""
			}`}
			aria-label="eReader transfer target. Drop library books here."
			onDrop={onDrop}
			onDragOver={onDragOver}
		>
			<div className="ereader-top">
				<span>{status.connected ? "Connected" : "Waiting for device"}</span>
				<i />
			</div>
			<div className="ereader-screen">
				<div className="screen-paper">
					<strong>{status.device?.name ?? "Family eReader"}</strong>
					<span>{bookCount} books aboard</span>
					<small>Drop library books here to send them</small>
				</div>
			</div>
			<div className="ereader-foot">
				<span />
			</div>
		</section>
	);
}

function JobTray({ jobs }: { jobs: TransferJob[] }) {
	const visibleJobs = jobs.slice(0, 3);
	if (!visibleJobs.length) {
		return null;
	}

	return (
		<div className="job-tray" aria-live="polite">
			{visibleJobs.map((job) => {
				const progress = job.total
					? Math.round((job.completed / job.total) * 100)
					: 0;
				return (
					<div className={`job-card ${job.status}`} key={job.id}>
						<div>
							<strong>
								{job.direction === "library-to-device"
									? "Sending to eReader"
									: "Importing to library"}
							</strong>
							<span>
								{job.error ?? `${job.completed} of ${job.total} complete`}
							</span>
						</div>
						<progress value={progress} max={100}>
							{progress}%
						</progress>
					</div>
				);
			})}
		</div>
	);
}

function App() {
	const [view, setView] = useState<View>("home");
	const [libraryBooks, setLibraryBooks] = useState<LibraryBook[]>([]);
	const [deviceBooks, setDeviceBooks] = useState<DeviceBook[]>([]);
	const [libraryCollections, setLibraryCollections] =
		useState<LibraryCollections>(emptyCollections);
	const [deviceCollections, setDeviceCollections] = useState<CollectionItem[]>(
		[],
	);
	const [deviceStatus, setDeviceStatus] = useState<DeviceStatus>({
		connected: false,
		device: null,
		lastError: null,
	});
	const [libraryQuery, setLibraryQuery] = useState("");
	const [deviceQuery, setDeviceQuery] = useState("");
	const [selectedLibraryIds, setSelectedLibraryIds] = useState<number[]>([]);
	const [selectedDevicePaths, setSelectedDevicePaths] = useState<string[]>([]);
	const [activeCollection, setActiveCollection] = useState<string>("all");
	const [jobs, setJobs] = useState<TransferJob[]>([]);
	const [notice, setNotice] = useState("Warming up the family shelf...");
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);
	const [editingBook, setEditingBook] = useState<LibraryBook | null>(null);

	const hasLiveLibrary = libraryBooks.length > 0;
	const libraryForDisplay = hasLiveLibrary ? libraryBooks : demoLibrary;
	const filteredLibrary = useMemo(() => {
		const byCollection =
			activeCollection === "all"
				? libraryForDisplay
				: libraryForDisplay.filter((book) => {
						const collection = activeCollection.toLowerCase();
						return (
							book.tags.some((tag) => tag.toLowerCase() === collection) ||
							book.authors.some(
								(author) => author.toLowerCase() === collection,
							) ||
							book.series?.toLowerCase() === collection
						);
					});
		return byCollection
			.filter((book) => matchesBook(book, libraryQuery))
			.sort(byTitle);
	}, [activeCollection, libraryForDisplay, libraryQuery]);

	const filteredDevice = useMemo(
		() =>
			deviceBooks
				.filter((book) => matchesBook(book, deviceQuery))
				.sort(byTitle),
		[deviceBooks, deviceQuery],
	);

	const allCollectionItems = useMemo(() => {
		if (
			libraryCollections.tags.length ||
			libraryCollections.authors.length ||
			libraryCollections.series.length
		) {
			return [
				...libraryCollections.tags,
				...libraryCollections.series,
				...libraryCollections.authors,
			];
		}
		const counts = new Map<string, number>();
		for (const book of libraryForDisplay) {
			for (const value of [...book.tags, ...book.authors, book.series ?? ""]) {
				if (value) {
					counts.set(value, (counts.get(value) ?? 0) + 1);
				}
			}
		}
		return [...counts.entries()]
			.map(([name, bookCount]) => ({ id: name, name, bookCount }))
			.sort((a, b) => a.name.localeCompare(b.name));
	}, [libraryCollections, libraryForDisplay]);

	const selectedLibraryBooks = libraryBooks.filter((book) =>
		selectedLibraryIds.includes(book.id),
	);
	const editorBook = editingBook ?? selectedLibraryBooks[0] ?? null;

	const loadLibraryBooks = useCallback(async () => {
		const response = await apiRequest<{ books: LibraryBook[] }>(
			"/api/library/books",
		);
		setLibraryBooks(response.books);
	}, []);

	const loadLibraryCollections = useCallback(async () => {
		const response = await apiRequest<LibraryCollections>(
			"/api/library/collections",
		);
		setLibraryCollections(response);
	}, []);

	const loadDeviceStatus = useCallback(async () => {
		const response = await apiRequest<DeviceStatus>("/api/device");
		setDeviceStatus(response);
	}, []);

	const loadDeviceBooks = useCallback(async () => {
		try {
			const response = await apiRequest<{ books: DeviceBook[] }>(
				"/api/device/books",
			);
			setDeviceBooks(response.books);
		} catch (caught) {
			if (caught instanceof Error && caught.message.includes("No e-reader")) {
				setDeviceBooks([]);
				return;
			}
			throw caught;
		}
	}, []);

	const loadDeviceCollections = useCallback(async () => {
		try {
			const response = await apiRequest<{ collections: CollectionItem[] }>(
				"/api/device/collections",
			);
			setDeviceCollections(response.collections);
		} catch (caught) {
			if (caught instanceof Error && caught.message.includes("No e-reader")) {
				setDeviceCollections([]);
				return;
			}
			throw caught;
		}
	}, []);

	const refreshAll = useCallback(async () => {
		setBusy(true);
		setError(null);
		try {
			await Promise.all([
				loadDeviceStatus(),
				loadLibraryBooks(),
				loadLibraryCollections(),
				loadDeviceBooks(),
				loadDeviceCollections(),
			]);
			setNotice("Shelf synced with the Pi.");
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "Could not reach the Pi backend",
			);
			setNotice("Using the quiet demo shelf until the backend responds.");
		} finally {
			setBusy(false);
		}
	}, [
		loadDeviceStatus,
		loadDeviceBooks,
		loadDeviceCollections,
		loadLibraryBooks,
		loadLibraryCollections,
	]);

	useEffect(() => {
		// Defer the first refresh so the effect body doesn't synchronously set state
		// (satisfies react-hooks/set-state-in-effect in eslint).
		void Promise.resolve().then(() => {
			void refreshAll();
		});
	}, [refreshAll]);

	useEffect(() => {
		const source = new EventSource(apiUrl("/api/events"));
		const refreshDevice = () => {
			void Promise.all([
				loadDeviceStatus(),
				loadDeviceBooks(),
				loadDeviceCollections(),
			]);
		};
		const refreshLibrary = () => {
			void Promise.all([loadLibraryBooks(), loadLibraryCollections()]);
		};
		const updateJob = (event: MessageEvent<string>) => {
			const body = JSON.parse(event.data) as {
				payload?: { job?: TransferJob };
			};
			const job = body.payload?.job;
			if (!job) {
				return;
			}
			setJobs((current) => [
				job,
				...current.filter((item) => item.id !== job.id),
			]);
			if (job.status === "completed") {
				refreshDevice();
				refreshLibrary();
			}
		};

		source.addEventListener("device.connected", () => {
			setNotice("eReader connected. The shelf is ready to trade books.");
			refreshDevice();
		});
		source.addEventListener("device.disconnected", () => {
			setNotice("eReader disconnected.");
			refreshDevice();
		});
		source.addEventListener("device.updated", refreshDevice);
		source.addEventListener("library.updated", refreshLibrary);
		source.addEventListener("transfer.queued", updateJob);
		source.addEventListener("transfer.started", updateJob);
		source.addEventListener("transfer.progress", updateJob);
		source.addEventListener("transfer.completed", updateJob);
		source.addEventListener("transfer.failed", updateJob);
		source.onerror = () => {
			setNotice("Live updates paused. Retrying connection to the Pi...");
		};

		return () => source.close();
	}, [
		loadDeviceStatus,
		loadDeviceBooks,
		loadDeviceCollections,
		loadLibraryBooks,
		loadLibraryCollections,
	]);

	function dragLibraryBooks(event: DragEvent<HTMLElement>, bookId: number) {
		const bookIds = selectedLibraryIds.includes(bookId)
			? selectedLibraryIds
			: [bookId];
		const payload: DragPayload = { source: "library", bookIds };
		event.dataTransfer.setData("application/json", JSON.stringify(payload));
		event.dataTransfer.effectAllowed = "copy";
	}

	function dragDeviceBooks(event: DragEvent<HTMLElement>, path: string) {
		const paths = selectedDevicePaths.includes(path)
			? selectedDevicePaths
			: [path];
		const payload: DragPayload = { source: "device", paths };
		event.dataTransfer.setData("application/json", JSON.stringify(payload));
		event.dataTransfer.effectAllowed = "copyMove";
	}

	function readDragPayload(event: DragEvent<HTMLElement>): DragPayload | null {
		const raw = event.dataTransfer.getData("application/json");
		if (!raw) {
			return null;
		}
		try {
			return JSON.parse(raw) as DragPayload;
		} catch {
			return null;
		}
	}

	function allowDrop(event: DragEvent<HTMLElement>) {
		event.preventDefault();
		event.dataTransfer.dropEffect = "copy";
	}

	async function dropOnDevice(event: DragEvent<HTMLElement>) {
		event.preventDefault();
		const payload = readDragPayload(event);
		if (payload?.source !== "library") {
			return;
		}
		await sendLibraryToDevice(payload.bookIds);
	}

	async function dropOnLibrary(event: DragEvent<HTMLElement>) {
		event.preventDefault();
		const payload = readDragPayload(event);
		if (payload?.source !== "device") {
			return;
		}
		await importDeviceToLibrary(payload.paths, false);
	}

	async function sendLibraryToDevice(bookIds: number[]) {
		if (!bookIds.length) {
			return;
		}
		if (!deviceStatus.connected) {
			setError("Connect an e-reader before sending books.");
			return;
		}
		setBusy(true);
		setError(null);
		try {
			const response = await apiRequest<{ job: TransferJob }>(
				"/api/transfers/library-to-device",
				{
					method: "POST",
					body: JSON.stringify({ bookIds, target: "main" }),
				},
			);
			setJobs((current) => [response.job, ...current]);
			setNotice(
				`Sending ${bookIds.length} book${bookIds.length === 1 ? "" : "s"} to the eReader.`,
			);
			setSelectedLibraryIds([]);
		} catch (caught) {
			setError(
				caught instanceof Error ? caught.message : "Could not start transfer",
			);
		} finally {
			setBusy(false);
		}
	}

	async function importDeviceToLibrary(paths: string[], deleteAfter: boolean) {
		if (!paths.length) {
			return;
		}
		setBusy(true);
		setError(null);
		try {
			const response = await apiRequest<{ job: TransferJob }>(
				"/api/transfers/device-to-library",
				{
					method: "POST",
					body: JSON.stringify({
						devicePaths: paths,
						deleteFromDeviceAfterCopy: deleteAfter,
					}),
				},
			);
			setJobs((current) => [response.job, ...current]);
			setNotice(
				`Importing ${paths.length} book${paths.length === 1 ? "" : "s"} into the Pi library.`,
			);
			setSelectedDevicePaths([]);
		} catch (caught) {
			setError(
				caught instanceof Error ? caught.message : "Could not start import",
			);
		} finally {
			setBusy(false);
		}
	}

	async function uploadBook(event: ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0];
		if (!file) {
			return;
		}
		setBusy(true);
		setError(null);
		const form = new FormData();
		form.append("file", file);
		try {
			const response = await apiRequest<{ book: LibraryBook }>(
				"/api/library/books",
				{
					method: "POST",
					body: form,
				},
			);
			setLibraryBooks((current) => [
				response.book,
				...current.filter((book) => book.id !== response.book.id),
			]);
			setNotice(`${response.book.title} joined the family library.`);
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : "Upload failed");
		} finally {
			setBusy(false);
			event.target.value = "";
		}
	}

	async function saveBook(book: LibraryBook, patch: Partial<LibraryBook>) {
		setBusy(true);
		setError(null);
		try {
			const response = await apiRequest<{ book: LibraryBook }>(
				`/api/library/books/${book.id}`,
				{
					method: "PATCH",
					body: JSON.stringify(patch),
				},
			);
			setLibraryBooks((current) =>
				current.map((item) =>
					item.id === response.book.id ? response.book : item,
				),
			);
			setEditingBook(response.book);
			setNotice("Book details saved.");
		} catch (caught) {
			setError(
				caught instanceof Error ? caught.message : "Could not save book",
			);
		} finally {
			setBusy(false);
		}
	}

	async function deleteSelectedBooks() {
		if (!selectedLibraryIds.length) {
			return;
		}
		setBusy(true);
		setError(null);
		try {
			await Promise.all(
				selectedLibraryIds.map((bookId) =>
					apiRequest<void>(`/api/library/books/${bookId}`, {
						method: "DELETE",
					}),
				),
			);
			setLibraryBooks((current) =>
				current.filter((book) => !selectedLibraryIds.includes(book.id)),
			);
			setNotice(
				`${selectedLibraryIds.length} book${selectedLibraryIds.length === 1 ? "" : "s"} removed from the library.`,
			);
			setSelectedLibraryIds([]);
		} catch (caught) {
			setError(
				caught instanceof Error ? caught.message : "Could not remove books",
			);
		} finally {
			setBusy(false);
		}
	}

	async function ejectDevice() {
		setBusy(true);
		setError(null);
		try {
			await apiRequest<void>("/api/device/eject", { method: "POST" });
			setDeviceStatus({ connected: false, device: null, lastError: null });
			setDeviceBooks([]);
			setNotice("The eReader is ready to unplug.");
		} catch (caught) {
			setError(
				caught instanceof Error ? caught.message : "Could not eject device",
			);
		} finally {
			setBusy(false);
		}
	}

	function toggleLibrarySelection(bookId: number) {
		setSelectedLibraryIds((current) =>
			current.includes(bookId)
				? current.filter((id) => id !== bookId)
				: [...current, bookId],
		);
	}

	function toggleDeviceSelection(path: string) {
		setSelectedDevicePaths((current) =>
			current.includes(path)
				? current.filter((item) => item !== path)
				: [...current, path],
		);
	}

	const nav = [
		{ id: "home" as const, label: "Home", mark: "H" },
		{ id: "library" as const, label: "Library", mark: "L" },
		{ id: "transfer" as const, label: "Transfer", mark: "T" },
		{ id: "manage" as const, label: "Manage", mark: "M" },
	];

	return (
		<div className="kiosk-shell">
			<aside className="wood-rail" aria-label="Main navigation">
				<div className="library-plaque">
					<span>Our</span>
					<strong>Library</strong>
				</div>
				<nav>
					{nav.map((item) => (
						<button
							type="button"
							className={view === item.id ? "active" : ""}
							key={item.id}
							onClick={() => setView(item.id)}
						>
							<span className="nav-mark" aria-hidden="true">
								{item.mark}
							</span>
							<span className="nav-label">{item.label}</span>
						</button>
					))}
				</nav>
				<div className="rail-status">
					<i className={deviceStatus.connected ? "lit" : ""} />
					<span>
						{deviceStatus.connected ? "eReader connected" : "No eReader"}
					</span>
				</div>
			</aside>

			<main className="tablet-stage">
				<header className="top-board">
					<div>
						<p>Family Bookshelf Kiosk</p>
						<h1>
							{view === "home"
								? "Good evening, family."
								: view === "library"
									? "The house library"
									: view === "transfer"
										? "Move books by touch"
										: "Care for the shelf"}
						</h1>
					</div>
					<div className="top-actions">
						<button
							type="button"
							onClick={() => void refreshAll()}
							disabled={busy}
						>
							Refresh
						</button>
						{deviceStatus.connected ? (
							<button
								type="button"
								onClick={() => void ejectDevice()}
								disabled={busy}
							>
								Eject
							</button>
						) : null}
					</div>
				</header>

				{error ? <div className="notice error">{error}</div> : null}
				<div className="notice">{notice}</div>

				{view === "home" ? (
					<section className="home-grid">
						<div className="home-hero">
							<div className="welcome-card paper-card">
								<div>
									<span className="eyebrow">
										Private, local, and made for hands
									</span>
									<h2>A family library that feels built into the house.</h2>
									<p>
										Browse the Pi library, connect an e-reader, and move books
										by dropping them onto the device. The screen keeps its own
										warm bookshelf presence when nobody is using it.
									</p>
								</div>
								<button
									type="button"
									className="kiosk-cta"
									onClick={() => setView("transfer")}
								>
									Open transfer
								</button>
							</div>
							<DeviceFigure
								status={deviceStatus}
								bookCount={deviceBooks.length}
							/>
						</div>
						<ul className="home-stats">
							<li className="stat-slat">
								<strong>{libraryBooks.length || demoLibrary.length}</strong>
								<span>library books</span>
							</li>
							<li className="stat-slat">
								<strong>{allCollectionItems.length}</strong>
								<span>collections</span>
							</li>
							<li className="stat-slat">
								<strong>{deviceBooks.length}</strong>
								<span>on eReader</span>
							</li>
						</ul>
						<div className="shelf-preview">
							{libraryForDisplay.slice(0, 6).map((book) => (
								<BookCover book={book} source="library" key={book.id} />
							))}
						</div>
					</section>
				) : null}

				{view === "library" ? (
					<section className="library-view">
						<aside className="collection-drawer">
							<button
								type="button"
								className={activeCollection === "all" ? "active" : ""}
								onClick={() => setActiveCollection("all")}
							>
								All Books <span>{libraryForDisplay.length}</span>
							</button>
							{allCollectionItems.slice(0, 16).map((collection) => (
								<button
									type="button"
									key={collection.id}
									className={
										activeCollection === collection.name ? "active" : ""
									}
									onClick={() => setActiveCollection(collection.name)}
								>
									{collection.name} <span>{collection.bookCount}</span>
								</button>
							))}
						</aside>
						<div className="bookshelf-panel">
							<div className="panel-tools">
								<label>
									Search library
									<input
										value={libraryQuery}
										onChange={(event) => setLibraryQuery(event.target.value)}
										placeholder="Title, author, collection..."
									/>
								</label>
								<button
									type="button"
									disabled={!selectedLibraryIds.length || busy}
									onClick={() => void sendLibraryToDevice(selectedLibraryIds)}
								>
									Send selected to eReader
								</button>
							</div>
							<div className="shelf-grid">
								{filteredLibrary.map((book) => (
									<BookCard
										book={book}
										source="library"
										key={book.id}
										selected={selectedLibraryIds.includes(book.id)}
										onToggle={() => toggleLibrarySelection(book.id)}
										onDragStart={(event) => dragLibraryBooks(event, book.id)}
										onOpen={
											book.id > 0 ? () => setEditingBook(book) : undefined
										}
									/>
								))}
							</div>
						</div>
					</section>
				) : null}

				{view === "transfer" ? (
					<section className="transfer-view">
						<div className="transfer-column paper-card">
							<div className="column-heading">
								<div>
									<span className="eyebrow">Pi library</span>
									<h2>Drag from the shelf</h2>
								</div>
								<input
									value={libraryQuery}
									onChange={(event) => setLibraryQuery(event.target.value)}
									placeholder="Find a book"
								/>
							</div>
							<div className="transfer-list">
								{filteredLibrary.slice(0, 18).map((book) => (
									<BookCard
										book={book}
										source="library"
										compact
										key={book.id}
										selected={selectedLibraryIds.includes(book.id)}
										onToggle={() => toggleLibrarySelection(book.id)}
										onDragStart={(event) => dragLibraryBooks(event, book.id)}
									/>
								))}
							</div>
						</div>

						<div className="device-transfer-pad">
							<DeviceFigure
								status={deviceStatus}
								bookCount={deviceBooks.length}
								isDropTarget
								onDragOver={allowDrop}
								onDrop={(event) => void dropOnDevice(event)}
							/>
							<button
								type="button"
								disabled={
									!selectedLibraryIds.length || busy || !deviceStatus.connected
								}
								onClick={() => void sendLibraryToDevice(selectedLibraryIds)}
							>
								Send selected onto eReader
							</button>
							<p>
								Select several books or drag one directly onto the e-reader
								screen.
							</p>
						</div>

						<section
							className="transfer-column paper-card receive-zone"
							aria-label="Import into the Pi library. Drop eReader books here."
							onDragOver={allowDrop}
							onDrop={(event) => void dropOnLibrary(event)}
						>
							<div className="column-heading">
								<div>
									<span className="eyebrow">Connected eReader</span>
									<h2>Drag back to library</h2>
								</div>
								<input
									value={deviceQuery}
									onChange={(event) => setDeviceQuery(event.target.value)}
									placeholder="Search device"
								/>
							</div>
							<div className="transfer-list">
								{filteredDevice.length ? (
									filteredDevice.map((book) => (
										<BookCard
											book={book}
											source="device"
											compact
											key={book.id}
											selected={selectedDevicePaths.includes(book.path)}
											onToggle={() => toggleDeviceSelection(book.path)}
											onDragStart={(event) => dragDeviceBooks(event, book.path)}
										/>
									))
								) : (
									<div className="empty-note">
										{deviceStatus.connected
											? "No readable books found on this eReader."
											: "Connect an eReader and its books will appear here."}
									</div>
								)}
							</div>
							<div className="button-row">
								<button
									type="button"
									disabled={!selectedDevicePaths.length || busy}
									onClick={() =>
										void importDeviceToLibrary(selectedDevicePaths, false)
									}
								>
									Import selected
								</button>
								<button
									type="button"
									disabled={!selectedDevicePaths.length || busy}
									onClick={() =>
										void importDeviceToLibrary(selectedDevicePaths, true)
									}
								>
									Import and remove
								</button>
							</div>
						</section>
					</section>
				) : null}

				{view === "manage" ? (
					<section className="manage-view">
						<div className="paper-card management-panel">
							<div className="panel-tools">
								<label>
									Add book to Pi library
									<input
										type="file"
										accept=".epub,.azw,.azw3,.mobi,.pdf,.txt,.kepub"
										onChange={(event) => void uploadBook(event)}
									/>
								</label>
								<button
									type="button"
									disabled={!selectedLibraryIds.length || busy}
									onClick={() => void deleteSelectedBooks()}
								>
									Remove selected
								</button>
							</div>
							<div className="manage-body">
								<div className="manage-list">
									{filteredLibrary.map((book) => (
										<BookCard
											book={book}
											source="library"
											compact
											key={book.id}
											selected={selectedLibraryIds.includes(book.id)}
											onToggle={() => toggleLibrarySelection(book.id)}
											onDragStart={(event) => dragLibraryBooks(event, book.id)}
											onOpen={
												book.id > 0 ? () => setEditingBook(book) : undefined
											}
										/>
									))}
								</div>
								<BookEditor
									key={
										editorBook
											? `${editorBook.id}:${editorBook.title}`
											: "empty"
									}
									book={editorBook}
									onSave={(book, patch) => void saveBook(book, patch)}
								/>
							</div>
						</div>
						<div className="paper-card collection-ledger">
							<h2>Collections</h2>
							<Ledger title="Tags" items={libraryCollections.tags} />
							<Ledger title="Series" items={libraryCollections.series} />
							<Ledger title="Authors" items={libraryCollections.authors} />
							<h2>eReader shelves</h2>
							<Ledger title="Device collections" items={deviceCollections} />
						</div>
					</section>
				) : null}

				<JobTray jobs={jobs} />
			</main>
		</div>
	);
}

function Ledger({ title, items }: { title: string; items: CollectionItem[] }) {
	return (
		<div className="ledger">
			<strong>{title}</strong>
			{items.length ? (
				items.slice(0, 8).map((item) => (
					<div key={item.id}>
						<span>{item.name}</span>
						<small>{item.bookCount}</small>
					</div>
				))
			) : (
				<p>No entries yet</p>
			)}
		</div>
	);
}

function BookEditor({
	book,
	onSave,
}: {
	book: LibraryBook | null;
	onSave: (book: LibraryBook, patch: Partial<LibraryBook>) => void;
}) {
	const [title, setTitle] = useState(book?.title ?? "");
	const [authors, setAuthors] = useState(book?.authors.join(", ") ?? "");
	const [tags, setTags] = useState(book?.tags.join(", ") ?? "");
	const [series, setSeries] = useState(book?.series ?? "");

	if (!book) {
		return (
			<div className="book-editor empty-note">
				Select a book to tune its title, author, collection tags, or series.
			</div>
		);
	}

	const canEdit = book.id > 0;
	const patch = {
		title,
		authors: uniqueSorted(authors.split(",").map((item) => item.trim())),
		tags: uniqueSorted(tags.split(",").map((item) => item.trim())),
		series: series.trim() || null,
	};

	return (
		<form
			className="book-editor"
			onSubmit={(event) => {
				event.preventDefault();
				if (canEdit) {
					onSave(book, patch);
				}
			}}
		>
			<div className="editor-head">
				<BookCover book={book} source="library" />
				<div>
					<span className="eyebrow">Selected book</span>
					<h2>{book.title}</h2>
					<p>{book.formats.join(" / ") || "No format detected"}</p>
				</div>
			</div>
			<label>
				Title
				<input
					value={title}
					disabled={!canEdit}
					onChange={(event) => setTitle(event.target.value)}
				/>
			</label>
			<label>
				Authors
				<input
					value={authors}
					disabled={!canEdit}
					onChange={(event) => setAuthors(event.target.value)}
				/>
			</label>
			<label>
				Collections / tags
				<input
					value={tags}
					disabled={!canEdit}
					onChange={(event) => setTags(event.target.value)}
				/>
			</label>
			<label>
				Series
				<input
					value={series}
					disabled={!canEdit}
					onChange={(event) => setSeries(event.target.value)}
				/>
			</label>
			<button type="submit" disabled={!canEdit}>
				Save details
			</button>
			{!canEdit ? (
				<p>
					Demo books can be browsed, but only Pi library books can be edited.
				</p>
			) : null}
		</form>
	);
}

export default App;
