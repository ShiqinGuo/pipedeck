import { invoke, isTauri } from '@tauri-apps/api/core';
import { ExternalLink } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

export function ApplicationLink({ url }: { url: string }) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const open = async () => {
      setPending(true);
      setFailed(false);
      try { await invoke('open_local_application', { url }); }
      catch { setFailed(true); }
      finally { setPending(false); }
  };
  if (!isTauri()) return <Button asChild size="sm"><a href={url} target="_blank" rel="noreferrer"><ExternalLink />{t('integration.open')}</a></Button>;
  return <div className="grid max-w-64 gap-1">
    <Button size="sm" disabled={pending} onClick={() => void open()}><ExternalLink />{t(pending ? 'integration.opening' : 'integration.open')}</Button>
    {failed && <p role="alert" className="text-xs text-danger">{t('integration.openFailed')}</p>}
  </div>;
}
