import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { Component, type ErrorInfo, type ReactNode } from 'react';
import i18n from '@/i18n';

interface Props {
  children: ReactNode;
}
interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('UI error', error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Stack
        role="alert"
        spacing={2}
        alignItems="center"
        justifyContent="center"
        sx={{ minHeight: '60vh', p: 3, textAlign: 'center' }}
      >
        <Typography variant="h2">{i18n.t('errors.boundaryTitle')}</Typography>
        <Typography color="text.secondary">{i18n.t('errors.boundaryText')}</Typography>
        <Button variant="contained" onClick={() => window.location.reload()}>
          {i18n.t('errors.reload')}
        </Button>
      </Stack>
    );
  }
}
