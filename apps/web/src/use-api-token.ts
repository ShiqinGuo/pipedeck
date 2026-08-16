import { useQuery } from '@tanstack/react-query';

import { resolveApiToken } from './api';

export function useApiToken() {
  const query = useQuery({ queryKey: ['api-token'], queryFn: resolveApiToken, staleTime: Infinity, retry: false });
  return {
    configured: Boolean(query.data),
    loading: query.isLoading,
    error: query.error,
    disabledReason: query.isLoading ? '正在读取本地写入令牌' : query.isError ? '无法读取本地写入令牌' : query.data ? null : '未配置本地写入令牌',
  };
}
