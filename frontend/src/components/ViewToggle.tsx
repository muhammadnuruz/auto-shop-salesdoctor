import GridViewIcon from '@mui/icons-material/GridView';
import ViewListIcon from '@mui/icons-material/ViewList';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import type { SxProps, Theme } from '@mui/material/styles';
import { useTranslation } from 'react-i18next';
import type { ViewMode } from '@/hooks/useViewMode';

export function ViewToggle({
  value,
  onChange,
  sx,
}: {
  value: ViewMode;
  onChange: (v: ViewMode) => void;
  sx?: SxProps<Theme>;
}) {
  const { t } = useTranslation();
  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      value={value}
      aria-label={t('order.catalogView')}
      onChange={(_, v: ViewMode | null) => v && onChange(v)}
      sx={{ flexShrink: 0, ...sx }}
    >
      <ToggleButton value="list" aria-label={t('order.viewList')} title={t('order.viewList')}>
        <ViewListIcon />
      </ToggleButton>
      <ToggleButton value="grid" aria-label={t('order.viewGrid')} title={t('order.viewGrid')}>
        <GridViewIcon />
      </ToggleButton>
    </ToggleButtonGroup>
  );
}
