document.addEventListener('DOMContentLoaded', function() {
    // --- Floating Search Toggle ---
    const searchIconButton = document.querySelector('.search-icon-button');
    const floatingSearch = document.querySelector('.floating-search');
    const searchInput = document.querySelector('.floating-search .search-input');


    if (searchIconButton) {
        searchIconButton.addEventListener('click', function(event) {
            event.preventDefault(); // Prevent default button behavior
            floatingSearch.classList.toggle('active');
            if (floatingSearch.classList.contains('active')) {
                searchInput.focus();
            }
        });
    }

    // Hide search box when clicking outside
    document.addEventListener('click', function(event) {
        if (!searchIconButton.contains(event.target) && !floatingSearch.contains(event.target) && floatingSearch.classList.contains('active')) {
            floatingSearch.classList.remove('active');
        }
    });
     // Hide search box when pressing outside
    searchInput.addEventListener('blur', function() {
            if (!searchInput.value.trim()) { // Check if input is empty
                floatingSearch.classList.remove('active');
            }
        });

    // --- Slideshow Logic ---
    // --- Mobile Slideshow ---
    let mobileSlideIndex = 0;
    const mobileSlides = document.querySelectorAll(".mobile-slideshow .mySlides"); // Target mobile slides
    const mobileDotsContainer = document.querySelector(".mobile-slideshow .slideshow-dots");
    let mobileDots = [];

    function mobilePlusSlides(n) {
        showMobileSlides(mobileSlideIndex += n);
    }

    function showMobileSlides(n) {
        if (!mobileSlides.length) return;

        if (n >= mobileSlides.length) { mobileSlideIndex = 0; }
        if (n < 0) { mobileSlideIndex = mobileSlides.length - 1; }

        for (let i = 0; i < mobileSlides.length; i++) {
            mobileSlides[i].classList.remove("active-slide");
            mobileSlides[i].style.display = "none";
        }

        updateMobileDots();

        mobileSlides[mobileSlideIndex].style.display = "block";
        mobileSlides[mobileSlideIndex].classList.add("active-slide");
    }

    function updateMobileDots() {
        if (!mobileDotsContainer) return;

        mobileDotsContainer.innerHTML = '';
        mobileDots = [];

        for (let i = 0; i < mobileSlides.length; i++) {
            const dot = document.createElement("span");
            dot.classList.add("dot");
            dot.addEventListener("click", () => {
                goToMobileSlide(i);
            });
            mobileDotsContainer.appendChild(dot);
            mobileDots.push(dot);
        }

        if (mobileDots.length > 0) {
            mobileDots[mobileSlideIndex].classList.add("active-dot");
        }
    }

    function goToMobileSlide(index) {
        mobileSlideIndex = index;
        showMobileSlides(mobileSlideIndex);
    }

   // --- Desktop Slideshow ---
    let desktopSlideIndex = 0;
    const desktopSlides = document.querySelectorAll(".desktop-slideshow .mySlides");

    function showDesktopSlides() {
        if (!desktopSlides.length) return;

        // Hide all slides and reset styles
        for (let i = 0; i < desktopSlides.length; i++) {
            desktopSlides[i].style.display = 'none';
            desktopSlides[i].classList.remove('active-slide');
            desktopSlides[i].style.transform = ''; // Reset any transform
            desktopSlides[i].style.opacity = '';    // Reset opacity
            desktopSlides[i].style.filter = '';     // Reset filter
        }

        // Calculate visible range, ensuring it's centered
        let numVisible = 5;
         if (window.innerWidth >= 992 && window.innerWidth <= 1199){
            numVisible = 4;
         }
        if (desktopSlides.length < numVisible) {
            numVisible = desktopSlides.length;
        }
        const startIndex = Math.max(0, desktopSlideIndex - Math.floor((numVisible - 1) / 2));
        const endIndex = Math.min(desktopSlides.length - 1, startIndex + numVisible - 1);


        // Display and style slides in the visible range
        for (let i = startIndex; i <= endIndex; i++) {
            desktopSlides[i].style.display = 'block'; // Display the slide

            // Apply styles based on whether it's the active slide or not
            if (i === desktopSlideIndex) {
                desktopSlides[i].classList.add('active-slide');
                desktopSlides[i].style.transform = 'scale(1.1)';
                desktopSlides[i].style.opacity = '1';
                desktopSlides[i].style.filter = 'none'; // Ensure no filter on active slide
            } else {
                desktopSlides[i].style.transform = 'scale(0.8)';
                desktopSlides[i].style.opacity = '0.7';
                desktopSlides[i].style.filter = 'brightness(0.5) blur(1px)'; // Reduced Blur
            }
        }
    }

     function desktopPlusSlides(n) {
        desktopSlideIndex += n;

        // Wrap around
        if (desktopSlideIndex >= desktopSlides.length) { desktopSlideIndex = 0; }
        if (desktopSlideIndex < 0) { desktopSlideIndex = desktopSlides.length - 1; }

        showDesktopSlides();
    }

    // Initial setup for both slideshows
    showMobileSlides(mobileSlideIndex);
    updateMobileDots();
    showDesktopSlides();


    // Automatic slideshow advance for mobile
    let mobileSlideInterval = setInterval(() => {
        mobilePlusSlides(1);
    }, 6000);

    // Pause slideshow on hover for mobile
    const mobileSlideshowContainer = document.querySelector(".mobile-slideshow");
    if (mobileSlideshowContainer) {
        mobileSlideshowContainer.addEventListener('mouseover', () => {
            clearInterval(mobileSlideInterval);
        });
        mobileSlideshowContainer.addEventListener('mouseout', () => {
            mobileSlideInterval = setInterval(() => {
                mobilePlusSlides(1);
            }, 6000);
        });
    }

    // Event listeners for mobile prev/next buttons
    const mobilePrevButton = document.querySelector(".mobile-slideshow .prev");
    const mobileNextButton = document.querySelector(".mobile-slideshow .next");

    if (mobilePrevButton) {
        mobilePrevButton.addEventListener('click', function(event) {
            event.preventDefault();
            mobilePlusSlides(-1);
        });
    }
    if (mobileNextButton) {
        mobileNextButton.addEventListener('click', function(event) {
            event.preventDefault();
            mobilePlusSlides(1);
        });
    }

    // --- Desktop Slideshow Controls ---
    // Automatic slideshow advance for DESKTOP (separate interval)
    let desktopSlideInterval = setInterval(() => {
        desktopPlusSlides(1);
    }, 6000);

    // Pause slideshow on hover for DESKTOP
    const desktopSlideshowContainer = document.querySelector(".desktop-slideshow");
    if (desktopSlideshowContainer) {
        desktopSlideshowContainer.addEventListener('mouseover', () => {
            clearInterval(desktopSlideInterval);
        });
        desktopSlideshowContainer.addEventListener('mouseout', () => {
            desktopSlideInterval = setInterval(() => {
                desktopPlusSlides(1);
            }, 6000);
        });
    }

    // Event listeners for prev/next buttons
    const prevButton = document.querySelector(".desktop-slideshow .prev");
    const nextButton = document.querySelector(".desktop-slideshow .next");
    // --- Event listeners for prev/next buttons ---
    if (prevButton) {
        prevButton.addEventListener('click', function(event) {
            event.preventDefault();
            desktopPlusSlides(-1); // Use desktopPlusSlides
        });
    }
    if (nextButton) {
        nextButton.addEventListener('click', function(event) {
            event.preventDefault();
           desktopPlusSlides(1);   // Use desktopPlusSlides
        });
    }
});
