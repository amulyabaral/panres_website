document.addEventListener('DOMContentLoaded', () => {
    const menuButton = document.getElementById('menuButton');
    const closeNavButton = document.getElementById('closeNav');
    const mobileNav = document.getElementById('mobileNav');

    if (menuButton && closeNavButton && mobileNav) {
        menuButton.addEventListener('click', () => {
            mobileNav.classList.remove('hidden');
            // Optional: Add animation classes if desired
        });

        closeNavButton.addEventListener('click', () => {
            mobileNav.classList.add('hidden');
            // Optional: Add animation classes if desired
        });

        // Optional: Close nav if clicking outside the nav area
        // mobileNav.addEventListener('click', (event) => {
        //     if (event.target === mobileNav) {
        //         mobileNav.classList.add('hidden');
        //     }
        // });
    } else {
        console.warn('Mobile navigation elements not found.');
    }
}); 