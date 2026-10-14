import ConstructionIcon from '@mui/icons-material/Construction';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState } from '@/components/States';

export function PlaceholderPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t(titleKey)} />
      <EmptyState
        title={t('placeholder.title')}
        text={t('placeholder.text')}
        action={<ConstructionIcon color="disabled" />}
      />
    </>
  );
}
export const AuditPage = () => <PlaceholderPage titleKey="nav.audit" />;
