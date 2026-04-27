import type {ComponentType, ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import {
  Container,
  Hexagon,
  Lock,
  Search,
  Shield,
  Timer,
  type LucideProps,
} from 'lucide-react';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  Icon: ComponentType<LucideProps>;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Polite & Controlled',
    Icon: Timer,
    description: (
      <>
        Small batches, configurable sleep intervals, per-item cooldowns, and
        hourly API caps. Houndarr searches slowly and politely so your indexers
        stay healthy.
      </>
    ),
  },
  {
    title: 'Missing, Cutoff & Upgrade',
    Icon: Search,
    description: (
      <>
        Automatically searches for missing episodes, movies, albums, and
        books, plus items below your quality cutoff. An optional upgrade pass
        re-searches completed items for better releases. Each search type has
        independent controls and budgets.
      </>
    ),
  },
  {
    title: 'Multi-Instance',
    Icon: Hexagon,
    description: (
      <>
        Connect one or more Radarr, Sonarr, Lidarr, Readarr, and Whisparr
        instances, each with their own batch size, sleep interval, cooldown,
        and hourly cap settings.
      </>
    ),
  },
  {
    title: 'No Telemetry',
    Icon: Shield,
    description: (
      <>
        Zero outbound connections to analytics, error tracking, or
        developer-controlled servers. The only network traffic goes to your
        own *arr instances.
      </>
    ),
  },
  {
    title: 'Encrypted API Keys',
    Icon: Lock,
    description: (
      <>
        API keys are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)
        and are never sent back to the browser. Authentication uses bcrypt,
        signed sessions, and CSRF protection.
      </>
    ),
  },
  {
    title: 'Single Docker Container',
    Icon: Container,
    description: (
      <>
        Runs as a single container alongside your existing *arr stack. SQLite
        database, non-root execution, and a dark-themed web UI built with
        FastAPI, HTMX, and Tailwind CSS.
      </>
    ),
  },
];

function Feature({title, Icon, description}: FeatureItem) {
  return (
    <div className={clsx('col col--4')}>
      <div className={clsx('padding-horiz--md padding-vert--md', styles.featureCard)}>
        <div className={styles.featureIcon} aria-hidden="true">
          <Icon size={16} strokeWidth={2} />
        </div>
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
      </div>
    </div>
  );
}

export default function HomepageFeatures(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <div className="row">
          {FeatureList.map((props, idx) => (
            <Feature key={idx} {...props} />
          ))}
        </div>
      </div>
    </section>
  );
}
