document.addEventListener('DOMContentLoaded', function() {
    // --- Search Input Toggle ---
    const searchForm = document.querySelector('.search-form');
    const searchInput = document.querySelector('.search-input');
    const searchButton = document.querySelector('.search-button');
    const searchIconButton = document.querySelector('.search-icon-button');
    let searchExpanded = false;

    function toggleSearch() {
        if (searchExpanded) {
            searchInput.style.display = 'none';
            searchButton.style.display = 'none';
            searchExpanded = false;
        } else {
            searchInput.style.display = 'inline-block';
            searchButton.style.display = 'inline-block';
            searchInput.focus();
            searchExpanded = true;
        }
    }

    if (searchIconButton) {
        searchIconButton.addEventListener('click', function(event) {
            event.preventDefault();
            toggleSearch();
        });

        // Handle clicks outside search form (mobile close)
        document.addEventListener('click', function(event) {
            if (window.innerWidth <= 768) {
                if (!searchForm.contains(event.target) && searchExpanded) {
                    toggleSearch();
                }
            }
        });
    }

    // --- Slideshow Logic ---
    let slideIndex = 0;
    const slides = document.getElementsByClassName("mySlides");
    const dotsContainer = document.querySelector(".slideshow-dots");
    let dots = []; // Array to store the dot elements

    function plusSlides(n) {
        showSlides(slideIndex += n);
    }

    function showSlides(n) {
        if (!slides.length) return;

        if (n >= slides.length) { slideIndex = 0; }
        if (n < 0) { slideIndex = slides.length - 1; }

        for (let i = 0; i < slides.length; i++) {
            slides[i].classList.remove("active-slide");
            slides[i].style.display = "none"; // Hide all slides initially
        }

        // Update dots
        updateDots();

        slides[slideIndex].style.display = "block"; // Display current slide
        slides[slideIndex].classList.add("active-slide");
    }


     // Function to update the dots
    function updateDots() {
        if (!dotsContainer) return;

        // Remove existing dots
        dotsContainer.innerHTML = '';
        dots = [];

        // Create dots
        for (let i = 0; i < slides.length; i++) {
            const dot = document.createElement("span");
            dot.classList.add("dot");
            dot.addEventListener("click", () => {
                goToSlide(i);
             });
            dotsContainer.appendChild(dot);
            dots.push(dot);
        }
         // Set active dot
        if (dots.length > 0) {
            dots[slideIndex].classList.add("active-dot");
        }
    }
    function goToSlide(index) {
        slideIndex = index;
        showSlides(slideIndex);
    }

    // Initial setup
    showSlides(slideIndex);
    updateDots(); // Create dots initially

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

    // --- Event listeners for prev/next buttons ---
    const prevButton = document.querySelector(".prev");
    const nextButton = document.querySelector(".next");

    if (prevButton) {
        prevButton.addEventListener('click', function(event) {
            event.preventDefault();
            plusSlides(-1);
        });
    }
    if (nextButton) {
        nextButton.addEventListener('click', function(event) {
            event.preventDefault();
            plusSlides(1);
        });
    }
});
