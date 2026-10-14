import Autocomplete from '@mui/material/Autocomplete';
import Chip from '@mui/material/Chip';
import TextField from '@mui/material/TextField';

export interface ChipOption {
  id: string;
  label: string;
  active?: boolean;
  /** `label` already contains the id (e.g. "12 (5 clients)"): do not append "(id)". */
  full?: boolean;
}

interface Props {
  /** Known values (from reference data). Unknown ids can still be typed in. */
  options?: ChipOption[];
  loading?: boolean;
  label: string;
  value: string[];
  onChange: (v: string[]) => void;
  helperText?: string;
  error?: boolean;
  disabled?: boolean;
  name?: string;
}

/** Free-text multi-value input (Enter / comma commits a chip, blur commits pending text). */
export function ChipInput({
  label,
  value,
  onChange,
  helperText,
  error,
  disabled,
  name,
  options,
  loading,
}: Props) {
  const labels = new Map((options ?? []).map((o) => [o.id, o.label]));
  const fullIds = new Set((options ?? []).filter((o) => o.full).map((o) => o.id));
  const labelOf = (id: string) => labels.get(id) ?? id;
  return (
    <Autocomplete<string, true, false, true>
      multiple
      freeSolo
      disabled={disabled}
      options={(options ?? []).filter((o) => o.active !== false).map((o) => o.id)}
      loading={loading}
      filterOptions={(opts, state) => {
        const q = state.inputValue.trim().toLowerCase();
        return opts.filter(
          (id) =>
            !value.includes(id) &&
            (!q || id.toLowerCase().includes(q) || labelOf(id).toLowerCase().includes(q)),
        );
      }}
      getOptionLabel={(o) =>
        labels.has(o) ? (fullIds.has(o) ? (labels.get(o) ?? o) : `${labels.get(o)} (${o})`) : o
      }
      value={value}
      autoSelect
      onChange={(_, v) => onChange(v.map((x) => x.trim()).filter(Boolean))}
      renderTags={(tags, getTagProps) =>
        tags.map((tag, index) => {
          const { key, ...tagProps } = getTagProps({ index });
          return (
            <Chip
              key={key}
              label={labels.has(tag) ? labels.get(tag) : tag}
              size="small"
              {...tagProps}
            />
          );
        })
      }
      renderInput={(params) => (
        <TextField
          {...params}
          name={name}
          label={label}
          error={error}
          helperText={helperText}
          inputProps={{ ...params.inputProps, autoCapitalize: 'off' }}
        />
      )}
    />
  );
}
