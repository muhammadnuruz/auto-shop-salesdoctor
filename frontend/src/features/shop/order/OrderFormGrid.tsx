import HistoryIcon from '@mui/icons-material/History';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import dayjs from 'dayjs';
import { useTranslation } from 'react-i18next';
import type { HeaderField, Order, OrderHeaderPatch, OptionGroup, ShopMe } from '@/api/types';

const GROUP_OF: Record<HeaderField, keyof ShopMe['options']> = {
  agent: 'agents',
  price_type: 'price_types',
  warehouse: 'warehouses',
  trade: 'trade_directions',
};

interface Props {
  order: Order;
  options: ShopMe['options'];
  editable: boolean;
  /** pending (not yet saved) header values */
  pending: (k: keyof OrderHeaderPatch) => OrderHeaderPatch[keyof OrderHeaderPatch] | undefined;
  onChange: (patch: OrderHeaderPatch) => void;
  lastPriceType: string | null;
}

function HeaderSelect({
  field,
  order,
  group,
  editable,
  value,
  onChange,
  endAdornment,
}: {
  field: HeaderField;
  order: Order;
  group: OptionGroup | null | undefined;
  editable: boolean;
  value: string | null | undefined;
  onChange: (v: string) => void;
  endAdornment?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const label = t(`order.field_${field}`);
  const current = order[field];
  const opts = group?.options ?? [];
  const selected = value !== undefined ? (value ?? '') : (current?.id ?? '');
  // fixed (or a single choice): shown as a disabled preset
  const preset = group?.mode === 'fixed' || opts.length <= 1;
  const known = opts.some((o) => o.id === selected);
  return (
    <TextField
      select
      label={label}
      value={selected}
      disabled={!editable || preset}
      onChange={(e) => onChange(e.target.value)}
      slotProps={{
        select: { displayEmpty: true },
        input: endAdornment ? { endAdornment } : undefined,
        inputLabel: { shrink: true },
      }}
      fullWidth
    >
      {opts.length === 0 && selected === '' && (
        <MenuItem value="" disabled>
          {t('order.noOptions')}
        </MenuItem>
      )}
      {selected === '' && opts.length > 1 && (
        <MenuItem value="">
          <em>{t('order.choose')}</em>
        </MenuItem>
      )}
      {!known && selected !== '' && (
        <MenuItem value={selected}>{current?.name ?? selected}</MenuItem>
      )}
      {opts.map((o) => (
        <MenuItem key={o.id} value={o.id}>
          {o.name ?? o.id}
        </MenuItem>
      ))}
    </TextField>
  );
}

/** Дата заявки | Дата отгрузки | Агент | Тип цены | Склад | Направление торговли. */
export function OrderFormGrid({
  order,
  options,
  editable,
  pending,
  onChange,
  lastPriceType,
}: Props) {
  const { t } = useTranslation();
  const today = dayjs().format('YYYY-MM-DD');
  const max = dayjs().add(180, 'day').format('YYYY-MM-DD');
  const ship = pending('shipment_date');
  const shipValue = ship !== undefined ? (ship ?? '') : (order.shipment_date ?? '');
  const priceType = pending('price_type');
  const selectedPrice = priceType !== undefined ? priceType : (order.price_type?.id ?? null);
  const canRepeatPrice =
    editable &&
    !!lastPriceType &&
    lastPriceType !== selectedPrice &&
    (options.price_types?.options ?? []).some((o) => o.id === lastPriceType);
  const field = (f: HeaderField, end?: React.ReactNode) => (
    <HeaderSelect
      key={f}
      field={f}
      order={order}
      group={options[GROUP_OF[f]]}
      editable={editable}
      value={pending(f) as string | null | undefined}
      onChange={(v) => onChange({ [f]: v || null })}
      endAdornment={end}
    />
  );
  return (
    <div
      style={{
        display: 'grid',
        gap: 16,
        gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
      }}
    >
      <TextField
        label={t('order.requestDate')}
        value={dayjs(order.created_at).format('DD.MM.YYYY')}
        disabled
        slotProps={{ inputLabel: { shrink: true } }}
      />
      <TextField
        type="date"
        label={t('order.shipmentDate')}
        value={shipValue}
        disabled={!editable}
        onChange={(e) => onChange({ shipment_date: e.target.value || null })}
        slotProps={{
          inputLabel: { shrink: true },
          htmlInput: { min: today, max },
        }}
      />
      {field('agent')}
      {field(
        'price_type',
        canRepeatPrice ? (
          <InputAdornment position="end" sx={{ mr: 2 }}>
            <Tooltip title={t('order.lastPriceType')}>
              <IconButton
                size="small"
                aria-label={t('order.lastPriceType')}
                onClick={() => onChange({ price_type: lastPriceType })}
              >
                <HistoryIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </InputAdornment>
        ) : undefined,
      )}
      {field('warehouse')}
      {field('trade')}
    </div>
  );
}
