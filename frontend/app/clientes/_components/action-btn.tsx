import { COLOR_CLASSES } from './helpers';

export function ActionBtn({
  color, icon: Icon, title, onClick,
}: {
  color: string;
  icon: React.ElementType;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`flex h-7 w-7 items-center justify-center rounded text-xs transition-colors ${COLOR_CLASSES[color] ?? COLOR_CLASSES.slate}`}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}
