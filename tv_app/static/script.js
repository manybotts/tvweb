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
	const desktopSlideshowInner = document.querySelector(".desktop-slideshow .slideshow-inner");
	let desktopSlides = []; // We'll store *all* slides (including clones) here
	let slideWidth; // Store the calculated width of a single slide
    let numVisible;

	function setupDesktopSlideshow() {
		if (!desktopSlideshowInner) return; // Exit if no desktop slideshow

		const originalSlides = document.querySelectorAll(".desktop-slideshow .mySlides");
        if (originalSlides.length === 0) return; //Exit if their is no slides

        // Determine how many slides to clone based on viewport
        numVisible = calculateNumVisible();
        //Remove any previously created cloned Nodes
        const clonedSlides = desktopSlideshowInner.querySelectorAll('.clone');
        clonedSlides.forEach(clone => clone.remove());

		// Clone slides for infinite looping
		for (let i = 0; i < numVisible; i++) {
			const cloneStart = originalSlides[i % originalSlides.length].cloneNode(true);
			cloneStart.classList.add('clone');
			desktopSlideshowInner.appendChild(cloneStart);

			const cloneEnd = originalSlides[(originalSlides.length - 1 - i) % originalSlides.length].cloneNode(true);
			cloneEnd.classList.add('clone');
			desktopSlideshowInner.insertBefore(cloneEnd, desktopSlideshowInner.firstChild);
		}

		desktopSlides = Array.from(document.querySelectorAll(".desktop-slideshow .mySlides")); // Include clones
        slideWidth = desktopSlides[0].offsetWidth + parseInt(window.getComputedStyle(desktopSlides[0]).marginRight) + parseInt(window.getComputedStyle(desktopSlides[0]).marginLeft); // Correctly calculate width + margins
        desktopSlideIndex = numVisible; //important for the cloned items to be correctly positioned
		showDesktopSlides();
	}
	function calculateNumVisible()
	{
        if (window.innerWidth >= 1200) {
            return 5;
        } else if (window.innerWidth >= 992) {
           return 4
        } else {
            return 0; // Should not happen, mobile view is handled separately
        }

	}

	function showDesktopSlides() {

        let offset = -slideWidth * desktopSlideIndex;
        desktopSlideshowInner.style.transform = `translateX(${offset}px)`;

        // Apply styles.  Loop through *all* slides.
        for (let i = 0; i < desktopSlides.length; i++) {
            desktopSlides[i].classList.remove('active-slide');
            desktopSlides[i].style.transform = '';
            desktopSlides[i].style.opacity = '';
            desktopSlides[i].style.filter = '';

            // Determine if this slide is *logically* the active slide (considering clones)
            const logicalIndex = (i - numVisible + desktopSlides.length) % (desktopSlides.length- 2*numVisible);

            if (logicalIndex === desktopSlideIndex % (desktopSlides.length - 2 * numVisible)) {
               desktopSlides[i].classList.add('active-slide');
               desktopSlides[i].style.transform = 'scale(1.1)';
               desktopSlides[i].style.opacity = '1';
               desktopSlides[i].style.filter = 'none';

            }
            else {
              desktopSlides[i].style.transform = 'scale(0.8)';
              desktopSlides[i].style.opacity = '0.7';
              desktopSlides[i].style.filter = 'brightness(0.5) blur(1px)';
            }

        }

	}

	function desktopPlusSlides(n) {

        desktopSlideshowInner.style.transition = "transform 0.5s ease"; // Add transition
        desktopSlideIndex += n;
         // Check if we're at the beginning or end, and adjust index to loop
        showDesktopSlides();

        // Set a timeout to reset position *without* transition, after the slide transition
        if (desktopSlideIndex == (desktopSlides.length-numVisible))
        {
           setTimeout(() => {
                desktopSlideshowInner.style.transition = "none"; // Disable transition for instant jump
                desktopSlideIndex = numVisible;
                showDesktopSlides();

            }, 500); // 500ms matches the CSS transition time

        }
        else if(desktopSlideIndex == (numVisible-1) ){
            setTimeout(() => {
                desktopSlideshowInner.style.transition = "none"; // Disable transition for instant jump
                 desktopSlideIndex = desktopSlides.length-numVisible-1;
                showDesktopSlides();

            }, 500); // 500ms matches the CSS transition time
        }
	}
	// Set up the desktop slideshow initially, and recalculate on resize
	setupDesktopSlideshow();
	window.addEventListener('resize', setupDesktopSlideshow);


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
