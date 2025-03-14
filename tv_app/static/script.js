document.addEventListener('DOMContentLoaded', function() {
    const searchForm = document.querySelector('.search-form');
    const searchInput = document.querySelector('.search-input');
    const searchButton = document.querySelector('.search-button');
    const searchIconButton = document.querySelector('.search-icon-button');
    let searchExpanded = false;

    function toggleSearch() {
        if (window.innerWidth <= 768) { // Check if we're in the small-screen state
            if (searchExpanded) {
                // Hide the input, show the icon
                searchInput.style.display = 'none';
                searchIconButton.style.display = 'inline-block';
                searchButton.style.display = 'none';
                searchExpanded = false;
            } else {
                // Show the input, hide the icon
                searchInput.style.display = 'inline-block';
                searchButton.style.display = 'inline-block';
                searchIconButton.style.display = 'none';
                searchInput.focus(); // Put the cursor in the input
                searchExpanded = true;
            }
        }
    }

     function handleResize() {
        if (window.innerWidth > 768 && searchExpanded) {
			searchInput.style.display = 'inline-block';
            searchButton.style.display = 'inline-block';
            searchIconButton.style.display = 'none';
        } else if(window.innerWidth <= 768 && searchExpanded){
			searchInput.style.display = 'inline-block';
            searchButton.style.display = 'none';
            searchIconButton.style.display = 'none';
		} else if (window.innerWidth <= 768 && !searchExpanded) {
            searchInput.style.display = 'none';
            searchButton.style.display = 'none';
			searchIconButton.style.display = 'inline-block';
		}
    }

    searchIconButton.addEventListener('click', function(event) {
        if (window.innerWidth <= 768) {
             event.preventDefault(); // Prevent form submission on icon click
             toggleSearch();
        } //else form will submit normally
    });

    window.addEventListener('resize', handleResize);
	// Initialize slideshow
    showSlides(slideIndex);

    let slideIndex = 0; // Moved outside DOMContentLoaded

    function plusSlides(n) {
        showSlides(slideIndex += n);
    }

    function currentSlide(n) {
        showSlides(slideIndex = n - 1);
    }

    function showSlides(n) {
        let i;
        let slides = document.getElementsByClassName("mySlides");
        let dots = document.getElementsByClassName("dot");

        if (n >= slides.length) { slideIndex = 0; }
        if (n < 0) { slideIndex = slides.length - 1; }

        for (i = 0; i < slides.length; i++) {
            slides[i].style.display = "none";
        }
        for (i = 0; i < dots.length; i++) {
            dots[i].className = dots[i].className.replace(" active", "");
        }

        // Check if slides exist before trying to access them
        if (slides.length > 0) {
            slides[slideIndex].style.display = "block";
             slides[slideIndex].classList.add('active-slide'); // Add this line

            // Check if dots exist before trying to access them
            if (dots.length > 0) {
                dots[slideIndex].className += " active";
            }
        }
		setSlideBackgrounds(); // Set Slides backgrounds
    }

    // Function to set background images
    function setSlideBackgrounds() {
        const slides = document.getElementsByClassName("mySlides");
        const slideshowInner = document.querySelector('.slideshow-inner');

        if (slides.length > 0) {
             const prevIndex = (slideIndex - 1 + slides.length) % slides.length;
            const nextIndex = (slideIndex + 1) % slides.length;

            // Ensure that we only try to access images if the slides exist
            const currentImg = slides[slideIndex].querySelector('img') ? slides[slideIndex].querySelector('img').src : '';
            const prevImg = slides[prevIndex].querySelector('img') ? slides[prevIndex].querySelector('img').src : '';
            const nextImg = slides[nextIndex].querySelector('img') ? slides[nextIndex].querySelector('img').src : '';

            slideshowInner.style.backgroundImage = `linear-gradient(to right, rgba(0,0,0,0.8) 20%, rgba(0,0,0,0.2) 40%, rgba(0,0,0,0.2) 60%, rgba(0,0,0,0.8) 80%), url('${prevImg}'), url('${nextImg}'), url('${currentImg}')`;

        }
    }
    // Automatic slideshow advance
    let slideInterval = setInterval(() => { plusSlides(1); }, 5000);

     // Pause slideshow on hover
    const slideshowContainer = document.querySelector(".slideshow-container");

    if(slideshowContainer){
        slideshowContainer.addEventListener('mouseover', () => {
            clearInterval(slideInterval);
        });

        slideshowContainer.addEventListener('mouseout', () => {
            slideInterval = setInterval(() => { plusSlides(1); }, 5000);
        });
    }
});
