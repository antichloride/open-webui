<script lang="ts">
import Modal from '$lib/components/common/Modal.svelte';
import { getContext } from 'svelte';
import { getFeedbackById } from '$lib/apis/evaluations';
import Spinner from '$lib/components/common/Spinner.svelte';
import XMark from '$lib/components/icons/XMark.svelte';
import Messages from '$lib/components/chat/Messages.svelte';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
dayjs.extend(relativeTime);

const i18n = getContext('i18n');

interface Message {
	id: string;
	role: 'user' | 'assistant';
	content: string;
	timestamp: number;
	model?: string;
	done?: boolean;
}

interface ChatHistory {
	messages: Record<string, Message>;
	currentId: string | null;
}

interface ChatData {
	history: ChatHistory;
	models?: string[];
}

interface ChatSnapshot {
	chat: {
		title: string;
		chat: ChatData;
	};
}

interface FeedbackData {
	snapshot: ChatSnapshot;
	data?: {
		rating?: number;
		comment?: string;
		details?: {
			rating?: number;
		};
	};
}

export let show = false;
export let feedbackId = '';

let loading = false;
let feedbackData: FeedbackData | null = null;
let error: string | null = null;
let history: ChatHistory = {
	messages: {},
	currentId: null
};
let selectedModels: string[] = [];
let autoScroll = true;
let prompt = '';
let atSelectedModel = null;

$: if (show && feedbackId) {
	loadFeedback();
}

async function loadFeedback() {
	loading = true;
	error = null;
	try {
		feedbackData = await getFeedbackById(localStorage.token, feedbackId);
		if (feedbackData?.snapshot?.chat?.chat?.history) {
			history = feedbackData.snapshot.chat.chat.history;
			selectedModels = feedbackData.snapshot.chat.chat.models || [];
		}
	} catch (e: unknown) {
		error = e instanceof Error ? e.message : 'An error occurred';
	} finally {
		loading = false;
	}
}
</script>

<Modal bind:show>
	<div class="text-gray-700 dark:text-gray-100">
		<div class="flex justify-between dark:text-gray-300 px-5 pt-4 pb-1">
			<div class="text-lg font-medium self-center">
				{#if loading}
					<div class="flex items-center gap-2">
						<Spinner className="size-4" />
						<span>Loading...</span>
					</div>
				{:else if error}
					<span class="text-red-500">Error: {error}</span>
				{:else if feedbackData}
					{feedbackData.snapshot?.chat?.title || 'Chat Insights'}
				{:else}
					Chat Insights
				{/if}
			</div>
			<button
				class="self-center"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className="size-5" />
			</button>
		</div>

		{#if feedbackData?.data?.details?.rating || feedbackData?.data?.comment}
			<div class="px-5 pb-2 text-sm text-gray-600 dark:text-gray-400">
				{#if feedbackData?.data?.details?.rating}
					<div class="mb-1">Rating: {feedbackData.data.details.rating}/10</div>
				{/if}
				{#if feedbackData?.data?.comment}
					<div class="italic">"{feedbackData.data.comment}"</div>
				{/if}
			</div>
		{/if}

		<div class="flex flex-col w-full px-4 pt-1 pb-4">
			{#if loading}
				<div class="flex justify-center items-center py-8">
					<Spinner className="size-8" />
				</div>
			{:else if error}
				<div class="text-red-500 text-center py-8">{error}</div>
			{:else if feedbackData?.snapshot?.chat?.chat?.history}
				<div class="h-[60vh] overflow-y-auto pointer-events-auto select-text">
					<Messages
						className="h-full flex pt-4 pb-8 pointer-events-auto select-text"
						chatId={feedbackId}
						readOnly={true}
						{selectedModels}
						{atSelectedModel}
						{prompt}
						bind:history
						bind:autoScroll
						sendPrompt={() => {}}
						continueResponse={() => {}}
						regenerateResponse={() => {}}
						mergeResponses={() => {}}
						chatActionHandler={() => {}}
						showMessage={() => {}}
						submitMessage={() => {}}
						addMessages={() => {}}
					/>
				</div>
			{:else}
				<div class="text-center py-8 text-gray-500">No chat history available</div>
			{/if}
		</div>
	</div>
</Modal> 