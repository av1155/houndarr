import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/quick-start',
        'getting-started/installation',
        'getting-started/kubernetes',
        'getting-started/helm',
        'getting-started/first-run-setup',
      ],
    },
    {
      type: 'category',
      label: 'Configuration',
      collapsed: false,
      items: [
        'configuration/environment-variables',
        'configuration/instance-settings',
        'configuration/reverse-proxy',
      ],
    },
    {
      type: 'category',
      label: 'Concepts',
      collapsed: false,
      items: [
        'concepts/how-houndarr-works',
        'concepts/troubleshooting',
        'concepts/faq',
        'concepts/test-coverage',
      ],
    },
    {
      type: 'category',
      label: 'Security',
      collapsed: false,
      items: [
        'security/trust-and-security',
      ],
    },
  ],
};

export default sidebars;
