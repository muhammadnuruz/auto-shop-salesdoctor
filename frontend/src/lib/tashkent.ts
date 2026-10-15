import dayjs, { type Dayjs } from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);
dayjs.extend(timezone);

/** The dealer's (and Sales Doctor's) time zone: report days are Tashkent calendar days. */
export const DEALER_TZ = 'Asia/Tashkent';

export const dealerNow = (): Dayjs => dayjs().tz(DEALER_TZ);
