import { useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from './client';
import type { components } from './schema';

type RunStatus = components['schemas']['RunStatus'];
type EventPage = components['schemas']['RunEventListResponse'];
type EventStream = EventPage & { terminalDrainComplete: boolean };

/** Keep the cursor with its run's query cache; an old request cannot publish into another run. */
export function useRunEventStream(runId: string, status: RunStatus | undefined) {
  const queryClient = useQueryClient();
  const queryKey = ['runs', 'detail', runId, 'events-cursor'] as const;
  const finished = status !== undefined && status !== 'queued' && status !== 'running';

  return useQuery<EventStream>({
    queryKey,
    queryFn: async ({ signal }) => {
      const previous = queryClient.getQueryData<EventStream>(queryKey);
      let after = previous?.next_after ?? 0;
      const events = [...(previous?.events ?? [])];
      for (;;) {
        signal.throwIfAborted();
        const page = await api.runEvents(runId, after);
        signal.throwIfAborted();
        if (page.next_after <= after) break;
        events.push(...page.events.filter((event) => event.run_id === runId && event.sequence > after));
        after = page.next_after;
        if (page.events.length === 0) break;
      }
      // A request started while running does not count as the final drain, even if it
      // finishes after the run changes to a terminal state. One more poll captures the tail.
      return { events, next_after: after, terminalDrainComplete: finished };
    },
    enabled: Boolean(runId) && status !== undefined,
    staleTime: finished ? Infinity : 1_000,
    refetchInterval: (query) => finished && query.state.data?.terminalDrainComplete ? false : 1_500,
    refetchOnWindowFocus: false,
  });
}
