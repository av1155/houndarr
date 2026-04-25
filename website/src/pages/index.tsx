import type {ComponentProps, ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Image from '@theme/IdealImage';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import Heading from '@theme/Heading';

import styles from './index.module.css';

type ScreenshotItem = {
  img: ComponentProps<typeof Image>['img'];
  alt: string;
  caption: string;
};

const DASHBOARD_SCREENSHOT: ScreenshotItem = {
  img: require('@site/static/img/screenshots/houndarr-dashboard-hero.png'),
  alt: 'Houndarr Dashboard: library-health bar, Recent hunts strip, and per-instance cards with WATCHING / ELIGIBLE / SEARCHED stats, Expected-to-unlock panel, policy chips, and Run Now button',
  caption: 'Dashboard: gating state at a glance, per-instance Expected-to-unlock panels, on-demand Run Now',
};

const SUPPORTING_SCREENSHOTS: ScreenshotItem[] = [
  {
    img: require('@site/static/img/screenshots/houndarr-logs.png'),
    alt: 'Houndarr Logs: filterable search activity log',
    caption: 'Logs',
  },
  {
    img: require('@site/static/img/screenshots/houndarr-settings-instances.png'),
    alt: 'Houndarr Settings: instance list with enable toggles',
    caption: 'Settings',
  },
  {
    img: require('@site/static/img/screenshots/houndarr-add-instance-form.png'),
    alt: 'Houndarr Add Instance: search, cutoff, and upgrade configuration',
    caption: 'Instance config',
  },
  {
    img: require('@site/static/img/screenshots/houndarr-settings-account.png'),
    alt: 'Houndarr Account settings: password and session management',
    caption: 'Account',
  },
  {
    img: require('@site/static/img/screenshots/houndarr-settings-help.png'),
    alt: 'Houndarr Help: in-app settings reference',
    caption: 'Help',
  },
];

type ScopeItem = {
  title: string;
  detail: string;
};

const SCOPE_EXCLUSIONS: ScopeItem[] = [
  {
    title: 'No download-client integration',
    detail: 'it triggers searches in your *arr instances, which handle downloads',
  },
  {
    title: 'No Prowlarr/indexer management',
    detail: 'your *arr instances manage their own indexers',
  },
  {
    title: 'No request workflows',
    detail: 'no Overseerr/Ombi-style request handling',
  },
  {
    title: 'No multi-user support',
    detail: 'single admin username and password',
  },
  {
    title: 'No media file manipulation',
    detail: 'it never touches your library files',
  },
];

type TypeBadge = {
  name: string;
  color: string;
};

const TYPE_BADGES: TypeBadge[] = [
  { name: 'Radarr',   color: '#fcd34d' },
  { name: 'Sonarr',   color: '#7dd3fc' },
  { name: 'Lidarr',   color: '#6ee7b7' },
  { name: 'Readarr',  color: '#fca5a5' },
  { name: 'Whisparr', color: '#f9a8d4' },
];

function TypeBadgeStrip() {
  return (
    <div className={styles.typeBadgeStrip}>
      <span className={styles.typeBadgeLabel}>Works with</span>
      {TYPE_BADGES.map((badge, idx) => (
        <span key={badge.name}>
          {idx > 0 && <span className={styles.typeBadgeSep}>·</span>}
          <span className={styles.typeBadge} style={{ color: badge.color }}>
            {badge.name}
          </span>
        </span>
      ))}
    </div>
  );
}

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  const logoSrc = useBaseUrl('/img/houndarr-logo-dark.png');
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className={clsx('container', styles.heroInner)}>

        {/* Left column: text content */}
        <div className={styles.heroLeft}>
          <img
            src={logoSrc}
            alt="Houndarr logo"
            className={styles.heroLogo}
          />
          <Heading as="h1" className={styles.heroTitle}>
            {siteConfig.title}
          </Heading>
          <p className={styles.heroTagline}>{siteConfig.tagline}</p>
          <TypeBadgeStrip />
          <div className={styles.heroCta}>
            <Link
              className="button button--primary button--lg"
              to="/docs/guides/installation/docker-compose">
              Get Started →
            </Link>
            <Link
              className={clsx('button button--outline button--lg', styles.githubButton)}
              href="https://github.com/av1155/houndarr">
              View on GitHub
            </Link>
          </div>
        </div>

        {/* Right column: dashboard preview */}
        <div className={clsx(styles.heroRight, 'landing-zoomable')}>
          <Image
            img={DASHBOARD_SCREENSHOT.img}
            alt={DASHBOARD_SCREENSHOT.alt}
            className={styles.heroScreenshot}
          />
        </div>

      </div>
    </header>
  );
}

function Screenshots() {
  return (
    <section className={clsx(styles.screenshots, 'landing-zoomable')}>
      <div className="container">
        <Heading as="h2" className="text--center margin-bottom--lg">
          See It in Action
        </Heading>

        {/* Hero: Dashboard takes full width */}
        <div className={styles.screenshotHero}>
          <Image img={DASHBOARD_SCREENSHOT.img} alt={DASHBOARD_SCREENSHOT.alt} />
          <p className={styles.screenshotCaption}>
            <strong>Dashboard</strong>: gating state at a glance, per-instance Expected-to-unlock panels, on-demand Run Now
          </p>
        </div>

        {/* Supporting grid: remaining screens */}
        <div className={styles.screenshotGallery}>
          {SUPPORTING_SCREENSHOTS.map((item) => (
            <div key={item.caption}>
              <Image img={item.img} alt={item.alt} />
              <p className={styles.screenshotCaption}>
                <strong>{item.caption}</strong>
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function WhatItDoes() {
  return (
    <section className={styles.whatItDoes}>
      <div className="container">
        <div className="row">
          <div className="col col--8 col--offset-2">
            <Heading as="h2" className="text--center margin-bottom--lg">
              Why Houndarr?
            </Heading>
            <p>
              Radarr, Sonarr, Lidarr, Readarr, and Whisparr monitor RSS feeds
              for new releases, but they do not go back and actively search for
              content already in your library that is missing or below your
              quality cutoff. Their built-in "Search All Missing" button fires
              every item at once, overwhelming indexer API limits.
            </p>
            <p>
              <strong>Houndarr searches slowly, politely, and automatically:</strong>{' '}
              small batches, configurable sleep intervals, per-item cooldowns,
              and hourly API caps. It runs as a single Docker container alongside
              your existing *arr stack.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

function WhatItDoesNot() {
  return (
    <section className={styles.scopeSection}>
      <div className="container">
        <div className="row">
          <div className="col col--8 col--offset-2">
            <Heading as="h3" className="text--center margin-bottom--md">
              Focused by Design
            </Heading>
            <ul>
              {SCOPE_EXCLUSIONS.map((item) => (
                <li key={item.title}>
                  <strong>{item.title}</strong>: {item.detail}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Polite media searching for your *arr stack"
      description="A self-hosted companion for Radarr, Sonarr, Lidarr, Readarr, and Whisparr that automatically searches for missing and upgrade-eligible media in polite, controlled batches.">
      <HomepageHeader />
      <main>
        <HomepageFeatures />
        <WhatItDoes />
        <Screenshots />
        <WhatItDoesNot />
      </main>
    </Layout>
  );
}
