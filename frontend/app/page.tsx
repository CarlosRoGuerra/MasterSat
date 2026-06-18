import dynamic from 'next/dynamic';
import { Inter } from 'next/font/google';
import { HeroSection } from '@/components/home/hero-section';
import { MetricsSection } from '@/components/home/metrics-section';
import { ChartSkeleton } from '@/components/home/skeleton';

const inter = Inter({ subsets: ['latin'], weight: ['400', '500', '600', '700'] });

const OverviewChartSection = dynamic(() => import('@/components/home/overview-chart-section'), {
  loading: () => <ChartSkeleton />,
});

export default function Home() {
  return (
    <main
      className={`${inter.className} fintech-home min-h-screen bg-[color:var(--fh-bg)] px-4 py-6 text-[color:var(--fh-text-primary)] sm:px-6 sm:py-10 md:px-8 lg:px-10 lg:py-14 xl:px-12`}
    >
      <div className="mx-auto max-w-7xl">
        <HeroSection />
        <MetricsSection />
        <OverviewChartSection />
      </div>
    </main>
  );
}
