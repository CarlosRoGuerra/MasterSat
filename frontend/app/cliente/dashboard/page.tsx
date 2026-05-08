import { redirect } from 'next/navigation';

export default function DisabledRoutePage() {
  redirect('/login/admin');
}
