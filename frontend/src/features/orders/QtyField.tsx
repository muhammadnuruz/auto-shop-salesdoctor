import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import { useState, type KeyboardEvent } from 'react';

interface Props {
  value: number;
  onChange: (n: number) => void;
  label: string;
  /** whole numbers only (blocks) */
  integer?: boolean;
  max?: number;
  disabled?: boolean;
  big?: boolean;
  /** data attributes used for Enter-moves-down keyboard navigation */
  navCol?: string;
  navRow?: number;
}

const fmt = (n: number): string => String(n);
const parse = (s: string, integer: boolean): number | null => {
  const clean = s.trim().replace(',', '.');
  if (clean === '') return null;
  if (!(integer ? /^\d+$/ : /^\d+(\.\d{0,3})?$/).test(clean)) return null;
  return Number(clean);
};

/** Quantity input with -/+ steppers. Emits only valid numbers; empty text commits 0 on blur. */
export function QtyField({
  value,
  onChange,
  label,
  integer = false,
  max = integer ? 100_000 : 1_000_000,
  disabled,
  big,
  navCol,
  navRow,
}: Props) {
  const [text, setText] = useState(fmt(value));
  const [prev, setPrev] = useState(value);
  if (prev !== value) {
    setPrev(value);
    if (parse(text, integer) !== value) setText(fmt(value));
  }

  const commit = (n: number) => {
    const next = Math.min(max, Math.max(0, Math.round(n * 1000) / 1000));
    setText(fmt(next));
    setPrev(next);
    if (next !== value) onChange(next);
  };
  const step = (dir: 1 | -1) => commit((parse(text, integer) ?? value) + dir);
  const size = big ? 44 : 30;

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      step(1);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      step(-1);
    }
  };

  return (
    <Stack direction="row" alignItems="center" spacing={0.25}>
      <IconButton
        size="small"
        aria-label={`${label} −`}
        disabled={disabled || value <= 0}
        onClick={() => step(-1)}
        sx={{ width: size, height: size }}
        tabIndex={-1}
      >
        <RemoveIcon fontSize="small" />
      </IconButton>
      <TextField
        value={text}
        disabled={disabled}
        size="small"
        onChange={(e) => {
          setText(e.target.value);
          const n = parse(e.target.value, integer);
          if (n !== null && n <= max) {
            setPrev(n);
            if (n !== value) onChange(n);
          }
        }}
        onBlur={() => {
          const n = parse(text, integer);
          if (n === null) commit(0);
          else setText(fmt(Math.min(n, max)));
        }}
        onFocus={(e) => e.target.select()}
        onKeyDown={onKeyDown}
        slotProps={{
          htmlInput: {
            'aria-label': label,
            inputMode: integer ? 'numeric' : 'decimal',
            'data-nav-col': navCol,
            'data-nav-row': navRow,
            style: { textAlign: 'center', padding: big ? '10px 4px' : '4px 2px' },
          },
        }}
        sx={{ width: big ? 72 : 60 }}
      />
      <IconButton
        size="small"
        aria-label={`${label} +`}
        disabled={disabled || value >= max}
        onClick={() => step(1)}
        sx={{ width: size, height: size }}
        tabIndex={-1}
      >
        <AddIcon fontSize="small" />
      </IconButton>
    </Stack>
  );
}
