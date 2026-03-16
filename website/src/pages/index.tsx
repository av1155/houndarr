import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import Heading from '@theme/Heading';

import styles from './index.module.css';

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
            className="button button--outline button--lg"
            style={{marginLeft: '1rem', color: '#fff', borderColor: '#fff'}}
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
        <div className="screenshot-gallery">
          <div>
            <img
              src={require('@site/static/img/screenshots/Dashboard_Houndarr.jpeg').default}
              alt="Houndarr Dashboard"
            />
            <p className="text--center margin-top--sm"><strong>Dashboard</strong></p>
          </div>
          <div>
            <img
              src={require('@site/static/img/screenshots/Logs_Houndarr.jpeg').default}
              alt="Houndarr Logs"
            />
            <p className="text--center margin-top--sm"><strong>Logs</strong></p>
          </div>
          <div>
            <img
              src={require('@site/static/img/screenshots/Settings_Houndarr.jpeg').default}
              alt="Houndarr Settings"
            />
            <p className="text--center margin-top--sm"><strong>Settings</strong></p>
          </div>
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
              Sonarr and Radarr monitor RSS feeds for new releases, but they do
              not go back and actively search for content already in your library
              that is missing or below your quality cutoff. Their built-in
              "Search All Missing" button fires every item at once, overwhelming
              indexer API limits.
            </p>
            <p>
              <strong>Houndarr searches slowly, politely, and automatically:</strong>{' '}
              small batches, configurable sleep intervals, per-item cooldowns,
              hourly API caps, and quiet hours. It runs as a single Docker
              container alongside your existing *arr stack.
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
              <li><strong>No download-client integration</strong> — it triggers searches in Sonarr/Radarr, which handle downloads</li>
              <li><strong>No Prowlarr/indexer management</strong> — your *arr instances manage their own indexers</li>
              <li><strong>No request workflows</strong> — no Overseerr/Ombi-style request handling</li>
              <li><strong>No multi-user support</strong> — single admin username and password</li>
              <li><strong>No media file manipulation</strong> — it never touches your library files</li>
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
      title="Polite media searching for Sonarr & Radarr"
      description="A self-hosted companion for Sonarr and Radarr that automatically searches for missing media in polite, controlled batches.">
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
