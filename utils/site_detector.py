"""Utility for detecting the CMS or type of a B2B/E-commerce website."""

import logging
from playwright.async_api import Page

logger = logging.getLogger("site-detector")

class SiteDetector:
    """Detects the CMS platform of a website using heuristics."""

    @staticmethod
    async def detect(page: Page) -> str:
        """
        Analyzes the page to detect the underlying platform.
        Returns one of: 'magento', 'shopify', 'woocommerce', 'generic'
        """
        # Run detection logic in browser context
        platform = await page.evaluate("""
            () => {
                // Check for Shopify
                if (window.Shopify || document.querySelector('script[src*="cdn.shopify.com"]')) {
                    return 'shopify';
                }
                
                // Check for Magento 2
                if (window.require && window.require.s && window.require.s.contexts._.config.baseUrl && window.require.s.contexts._.config.baseUrl.includes('magento') || 
                    document.querySelector('script[type="text/x-magento-init"]') ||
                    document.querySelector('html[data-container="body"]')) {
                    return 'magento';
                }
                
                // Check for WooCommerce/WordPress
                if (document.querySelector('body.woocommerce') || 
                    document.querySelector('link[href*="wp-content/plugins/woocommerce"]')) {
                    return 'woocommerce';
                }
                
                // Check meta generator tags as a fallback
                const generator = document.querySelector('meta[name="generator"]');
                if (generator) {
                    const content = generator.content.toLowerCase();
                    if (content.includes('shopify')) return 'shopify';
                    if (content.includes('magento')) return 'magento';
                    if (content.includes('woocommerce') || content.includes('wordpress')) return 'woocommerce';
                }
                
                return 'generic';
            }
        """)
        
        logger.info(f"[SITE-DETECTOR] Detected platform: {platform}")
        return platform
