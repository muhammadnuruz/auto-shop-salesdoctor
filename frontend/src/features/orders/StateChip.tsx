import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import { useTranslation } from 'react-i18next';
import type { OrderState } from '@/api/types';
import { sdStatusColor, stateColor } from './orderUtils';

export function StateChip({ state, sdStatus }: { state: OrderState; sdStatus?: number | null }) {
  const { t } = useTranslation();
  return (
    <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
      <Chip size="small" color={stateColor(state)} label={t(`orderState.${state}`)} />
      {sdStatus != null && state !== 'deleted' && (
        <Chip
          size="small"
          variant="outlined"
          color={sdStatusColor(sdStatus)}
          label={t(`sdStatus.s${sdStatus}`, { defaultValue: String(sdStatus) })}
        />
      )}
    </Stack>
  );
}
