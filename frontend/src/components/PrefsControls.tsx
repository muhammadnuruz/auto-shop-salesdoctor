import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import { useTranslation } from 'react-i18next';
import { LANGS, setLanguage, type Lang } from '@/i18n';
import { useColorMode } from '@/theme/ColorMode';

export function PrefsControls() {
  const { t, i18n } = useTranslation();
  const { mode, toggle } = useColorMode();
  const lang = (LANGS as readonly string[]).includes(i18n.language)
    ? (i18n.language as Lang)
    : 'ru';
  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <ToggleButtonGroup
        size="small"
        exclusive
        value={lang}
        aria-label={t('prefs.language')}
        onChange={(_, v: Lang | null) => v && setLanguage(v)}
        // Inherit the surrounding text colour: on the shop AppBar (primary or link branding
        // colour) a `color="primary"` selected button would be primary-on-primary, i.e. invisible.
        sx={{
          '& .MuiToggleButton-root': {
            color: 'inherit',
            borderColor: 'color-mix(in srgb, currentColor 45%, transparent)',
            fontWeight: 500,
            '&:hover': { bgcolor: 'color-mix(in srgb, currentColor 10%, transparent)' },
            '&.Mui-selected, &.Mui-selected:hover': {
              color: 'inherit',
              fontWeight: 700,
              bgcolor: 'color-mix(in srgb, currentColor 22%, transparent)',
            },
          },
        }}
      >
        <ToggleButton value="ru" aria-label="Русский" sx={{ px: 1 }}>
          RU
        </ToggleButton>
        <ToggleButton value="uz" aria-label="O'zbekcha" sx={{ px: 1 }}>
          UZ
        </ToggleButton>
      </ToggleButtonGroup>
      <Tooltip title={t('prefs.toggleTheme')}>
        <IconButton onClick={toggle} aria-label={t('prefs.toggleTheme')} color="inherit">
          {mode === 'dark' ? <LightModeIcon /> : <DarkModeIcon />}
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
