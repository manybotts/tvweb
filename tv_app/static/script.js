document.addEventListener('DOMContentLoaded', function() {
    // --- Search Input Toggle ---
    const searchForm = document.querySelector('.search-form');
    const searchInput = document.querySelector('.search-input');
    const searchButton = document.querySelector('.search-button');
    const searchIconButton = document.querySelector('.search-icon-button');
    let searchExpanded = false; // Keep track of the search input state

    // Function to toggle the search input visibility
    function toggleSearch() {
        if (searchExpanded) {
            searchInput.style.display = 'none';
            searchButton.style.display = 'none';
            searchIconButton.style.display = 'inline-block';
            searchExpanded = false;
        } else {
            searchInput.style.display = 'inline-block';
            searchButton.style.display = 'inline-block';
            searchIconButton.style.display = 'none';
            searchInput.focus();
            searchExpanded = true;
        }
    }

    // Event listener for the search icon button (mobile)
    if (searchIconButton) {
        searchIconButton.addEventListener('click', function(event) {
            event.preventDefault(); // Prevent form submission
            toggleSearch();
        });
    }

    // Event listener for clicks outside the search form (to close it)
    document.addEventListener('click', function(event) {
        if (window.innerWidth <= 768) {
            if (!searchForm.contains(event.target) && searchExpanded) {
                // Clicked outside the form while expanded
                toggleSearch(); // Hide the search input
            }
        }
    });

     // --- Slideshow Logic ---
    let slideIndex = 0;
    const slides = document.getElementsByClassName("mySlides");

    function plusSlides(n) {
        showSlides(slideIndex += n);
    }

   // No currentSlide function
    function showSlides(n) {
        if (!slides.length) return; // Exit if no slides

        if (n >= slides.length) { slideIndex = 0; }
        if (n < 0) { slideIndex = slides.length - 1; }

          // Remove 'active-slide' class from all slides
        for (let i = 0; i < slides.length; i++) {
            slides[i].classList.remove("active-slide");
        }

         // Add 'active-slide' class to the current slide
        if (slides.length > 0) {
            slides[slideIndex].classList.add("active-slide");
        }

        // Update background image (blurred)
        updateSlideshowBackground();
    }
    function updateSlideshowBackground() {
        const slideshowInner = document.querySelector('.slideshow-inner');
        if (!slideshowInner || slides.length === 0) return;

        // Get the image URLs for the previous, current, and next slides
        const prevIndex = (slideIndex - 1 + slides.length) % slides.length;
        const nextIndex = (slideIndex + 1) % slides.length;

        const currentImg = slides[slideIndex].querySelector('img')?.src || '';
        const prevImg = slides[prevIndex].querySelector('img')?.src || '';
        const nextImg = slides[nextIndex].querySelector('img')?.src || '';

        // Set the background images using CSS variables and a gradient
        slideshowInner.style.backgroundImage = `
            linear-gradient(to right, rgba(0,0,0,0.8) 20%, rgba(0,0,0,0.2) 40%, rgba(0,0,0,0.2) 60%, rgba(0,0,0,0.8) 80%),
            url('${prevImg}'),
            url('${nextImg}'),
            url('${currentImg}')
        `;
    }
// Call showSlides initially to set up the slideshow
    showSlides(slideIndex);

     // Automatic slideshow advance
    let slideInterval = setInterval(() => { plusSlides(1); }, 6000);

     // Pause slideshow on hover
    const slideshowContainer = document.querySelector(".slideshow-container");
    if(slideshowContainer){
        slideshowContainer.addEventListener('mouseover', () => {
            clearInterval(slideInterval);
        });

        slideshowContainer.addEventListener('mouseout', () => {
            slideInterval = setInterval(() => { plusSlides(1); }, 6000);
        });
    }

    // --- Helper function to set the active slide (used by prev/next and dots)---
    function setActiveSlide(index) {
        const slides = document.getElementsByClassName("mySlides");
        if (index >= 0 && index < slides.length) {
            slideIndex = index;
            showSlides(slideIndex);
        }
    }

    // --- Event listeners for prev/next buttons ---
    const prevButton = document.querySelector(".prev");
    const nextButton = document.querySelector(".next");

    if (prevButton) {
        prevButton.addEventListener('click', function(event) {
            event.preventDefault(); // Prevent default link behavior
            plusSlides(-1);
        });
    }
    if (nextButton) {
        nextButton.addEventListener('click', function(event) {
            event.preventDefault(); // Prevent default link behavior
            plusSlides(1);
        });
    }
});
