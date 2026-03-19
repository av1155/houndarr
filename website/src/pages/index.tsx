import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import Heading from '@theme/Heading';

import styles from './index.module.css';

type ScreenshotItem = {
  src: string;
  alt: string;
  caption: string;
};

const DASHBOARD_SCREENSHOT: ScreenshotItem = {
  src: require('@site/static/img/screenshots/Dashboard_Houndarr.jpeg').default,
  alt: 'Houndarr Dashboard — instance cards with search metrics and activity',
  caption: 'Dashboard — live search metrics, instance status, and on-demand triggers',
};

const SUPPORTING_SCREENSHOTS: ScreenshotItem[] = [
  {
    src: require('@site/static/img/screenshots/Logs_Houndarr.jpeg').default,
    alt: 'Houndarr Logs — filterable search activity log',
    caption: 'Logs',
  },
  {
    src: require('@site/static/img/screenshots/Settings_Houndarr.jpeg').default,
    alt: 'Houndarr Settings — instance list with enable toggles',
    caption: 'Settings',
  },
  {
    src: require('@site/static/img/screenshots/Settings_Houndarr_Add_Instance_Settings.jpeg').default,
    alt: 'Houndarr Add Instance — search and cutoff configuration',
    caption: 'Instance config',
  },
  {
    src: require('@site/static/img/screenshots/Settings_Account_Houndarr.jpeg').default,
    alt: 'Houndarr Account settings — password and session management',
    caption: 'Account',
  },
  {
    src: require('@site/static/img/screenshots/Settings_Help_Houndarr.jpeg').default,
    alt: 'Houndarr Help — in-app settings reference',
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

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className="container">
        <img
          src={require('@site/static/img/houndarr-logo-dark.png').default}
          alt="Houndarr logo"
          className={styles.heroLogo}
        />
        <Heading as="h1" className="hero__title">
          {siteConfig.title}
        </Heading>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link
            className="button button--secondary button--lg"
            to="/docs/getting-started/quick-start">
            Get Started
          </Link>
          <Link
            className={clsx('button button--outline button--lg', styles.githubButton)}
            href="https://github.com/av1155/houndarr">
            View on GitHub
          </Link>
        </div>
      </div>
    </header>
  );
}

function Screenshots() {
  return (
    <section className={styles.screenshots}>
      <div className="container">
        <Heading as="h2" className="text--center margin-bottom--lg">
          See It in Action
        </Heading>

        {/* Hero — Dashboard takes full width */}
        <div className={styles.screenshotHero}>
          <img src={DASHBOARD_SCREENSHOT.src} alt={DASHBOARD_SCREENSHOT.alt} />
          <p className={styles.screenshotCaption}>
            <strong>Dashboard</strong> — live search metrics, instance status, and on-demand triggers
          </p>
        </div>

        {/* Supporting grid — remaining screens */}
        <div className={styles.screenshotGallery}>
          {SUPPORTING_SCREENSHOTS.map((item) => (
            <div key={item.caption}>
              <img src={item.src} alt={item.alt} />
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
                  <strong>{item.title}</strong> — {item.detail}
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
      description="A self-hosted companion for Radarr, Sonarr, Lidarr, Readarr, and Whisparr that automatically searches for missing media in polite, controlled batches.">
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
